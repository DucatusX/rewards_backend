import json
import logging
from datetime import timedelta

from tortoise import timezone
from tortoise.transactions import atomic
from web3 import Web3

from src.consts import DECIMALS
from src.redis_utils import RedisClient
from src.rewards.models import Airdrop, AirdropStatus, Healthcheck, Peer, Rate, Reward
from src.settings import config
from src.utils import pubkey_to_address, request_active_enodes, get_xgen_nodes, get_redis_xgen_nodes, valid_enode

logger = logging.getLogger("src.rewards.tasks")


class AirdropError(Exception):
    pass


async def ping_nodes() -> None:
    logger.info("try ping nodes")
    active_enodes = await request_active_enodes()
    RedisClient().set("online_peers", json.dumps(list(active_enodes)), 5 * 60)

    logger.debug("active nodes: \n{}".format("\n".join(active_enodes)))
    xgen_nodes = await get_xgen_nodes()
    RedisClient().set("xgen_enodes_mapping", json.dumps(xgen_nodes), 5 * 60)
    for enode, _ in xgen_nodes.items():
        if not valid_enode(enode):
            continue

        peer, _ = await Peer.get_or_create(
            enode=enode,
            defaults={
                "reward_interest": round(config.default_usd_reward_amount / 100, 18)
            },
        )

        is_online = enode in active_enodes
        timestamp = timezone.now() - timedelta(days=1)
        healthcheck_count = await Healthcheck.filter(
            peer=peer, timestamp__gte=timestamp
        ).count()
        if healthcheck_count:
            healthcheck = (
                await Healthcheck.filter(peer=peer).order_by("-timestamp").first()
            )
        else:
            healthcheck = await Healthcheck.create(peer=peer)
        if is_online:
            healthcheck.online_counter += 1
            logger.info(f"{enode} is online")
        healthcheck.total_counter += 1
        await healthcheck.save()


async def send_rewards() -> None:
    try:
        airdrop = await create_airdrop()
    except AirdropError as e:
        logger.info(f"Exception create airdrop {e}")
        return

    await airdrop.relay()


@atomic()
async def create_airdrop() -> Airdrop:
    airdrop = await Airdrop.create()
    reward_count = 0
    xgen_nodes = await get_redis_xgen_nodes()
    for enode, wallet in xgen_nodes.items():
        if not valid_enode(enode):
            continue

        peer, _ = await Peer.get_or_create(
            enode=enode,
            defaults={
                "reward_interest": round(config.default_usd_reward_amount / 100, 18)
            },
        )

        healthcheck = (
            await peer.healthchecks.filter(total_counter__gte=10)
            .order_by("-timestamp")
            .first()
        )
        online_percent = int(
            healthcheck.online_counter * 100 / healthcheck.total_counter
        )
        logger.info(f"{peer.enode} online percent is {online_percent}%")

        if online_percent >= config.reward_min_percent:
            address_checksum = Web3.toChecksumAddress(wallet)
            amount = await Rate.count_reward_amount(
                float(peer.reward_interest), online_percent
            )
            await Reward.create(
                airdrop=airdrop,
                address=address_checksum,
                amount=amount,
            )
            reward_count += 1

    if not reward_count:
        raise AirdropError("Nothing to airdrop")

    logger.info("Airdrop created")
    return airdrop


@atomic()
async def check_pending_airdrops() -> None:
    airdrops = await Airdrop.filter(status=AirdropStatus.PENDING).select_for_update()
    logger.info(f"{len(airdrops)} pending airdrops")

    for airdrop in airdrops:
        await airdrop.check_relayed_tx()


@atomic()
async def check_waiting_airdrops() -> None:
    airdrops = await Airdrop.filter(
        status__in=(AirdropStatus.WAITING_FOR_RELAY, AirdropStatus.INSUFFICIENT_BALANCE)
    ).select_for_update()

    logger.info(f"{len(airdrops)} waiting airdrops")

    if airdrops:
        await airdrops[0].relay()


async def update_rates() -> None:
    """
    Get rate for reward currency from API and save to DB
    """
    try:
        rates = await config.api.rates
        for i_currency in rates:
            decimals = DECIMALS.get(i_currency, 0)
            rate, _ = await Rate.get_or_create(currency=i_currency, decimals=decimals)
            rate.usd_rate = rates.get(i_currency).get("USD")
            await rate.save()
    except Exception as err:
        logger.warning("Cant get rates from API cause {err}".format(err=err))


async def update_peer_addresses() -> None:
    unset_peers = await Peer.filter(pubkey_address=None).all()
    for peer in unset_peers:
        if not valid_enode(peer.enode):
            continue

        peer.pubkey_address = pubkey_to_address(peer.enode)
        await peer.save(update_fields=("pubkey_address",))
        logging.info(f"Set address {peer.pubkey_address} for peer {peer.enode}")
