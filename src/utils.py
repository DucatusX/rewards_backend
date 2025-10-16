import json
import logging
from typing import Set

import requests
from eth_keys import keys
from eth_utils.exceptions import ValidationError as EthUtilsValidationError
from requests.adapters import HTTPAdapter
from web3 import Web3

from src.redis_utils import RedisClient
from src.settings import config

logger = logging.getLogger("src.utils")


async def request_active_enodes() -> Set[str]:
    payload = {
        "method": "admin_peers",
        "params": [],
        "id": 1,
        "jsonrpc": "2.0",
    }

    headers = {"Content-Type": "application/json"}
    adapter = HTTPAdapter(max_retries=config.ping_nodes_max_retries)
    active_enodes = set()
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    for json_rpc in config.json_rpc_urls:
        res = session.post(
            json_rpc,
            json=payload,
            headers=headers,
            timeout=config.ping_nodes_retries_timeout_secs,
        )
        peers = res.json()["result"]
        for peer in peers:
            if peer["enode"]:
                peer_id = peer["enode"].split("@")[0][8:]
                active_enodes.add(peer_id)

    session.close()
    return active_enodes


async def get_xgen_nodes() -> dict[str, str]:
    result: dict[str, str] = {}
    adapter = HTTPAdapter(max_retries=config.ping_nodes_max_retries)

    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    resp = session.get(config.xgen_devices_api, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    devices = data.get("devices", [])
    for device in devices:
        enode = device.get("enode")
        wallet = device.get("wallet_address")
        if enode and wallet:
            enode_key = enode.split("@")[0][8:]
            result[enode_key] = wallet
    session.close()
    return result


def pubkey_to_address(pubkey: str) -> str:
    pub_key_bytes = Web3.toBytes(hexstr=pubkey)
    pub_key = keys.PublicKey(pub_key_bytes)
    return pub_key.to_checksum_address()


def valid_enode(enode: str) -> bool:
    if enode == "":
        return False
    try:
        _ = pubkey_to_address(enode)
        return True
    except EthUtilsValidationError:
        logging.warning(f"enode {enode} not valid, remove it from files and DB")
        return False


async def get_redis_online_peers() -> list:
    active_enodes = RedisClient().get("online_peers")
    if not active_enodes:
        active_enodes = await request_active_enodes()
        active_enodes = json.dumps(list(active_enodes))
        RedisClient().set("online_peers", active_enodes, 5 * 60)

    active_enodes = json.loads(active_enodes)
    return active_enodes


async def  get_redis_xgen_nodes() -> dict:
    xgen_nodes = RedisClient().get("xgen_enodes_mapping")
    if not xgen_nodes:
        xgen_nodes = await get_xgen_nodes()
        xgen_nodes = json.dumps(list(xgen_nodes))
        RedisClient().set("xgen_enodes_mapping", xgen_nodes, 5 * 60)

    xgen_nodes = json.loads(xgen_nodes)
    return xgen_nodes
