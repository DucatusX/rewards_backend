import contextlib
import glob
import logging.config
import os
from dataclasses import dataclass, field
from typing import List, Set

import yaml
from eth_account import Account
from marshmallow_dataclass import class_schema
from web3 import HTTPProvider, Web3, contract

from contracts import MULTISENDER_ABI
from src.core.rates_api import RatesAPI
from src.logging_conf.config import logger_config

logging.config.dictConfig(logger_config)
logging.getLogger("apscheduler.executors.default").propagate = False


POSTGRES_URL = "postgres://{user}:{password}@{hostname}:{port}/{db}".format(
    user=os.getenv("POSTGRES_USER", "rewards"),
    password=os.getenv("POSTGRES_PASSWORD", "rewards"),
    hostname=os.getenv("POSTGRES_HOST", "127.0.0.1"),
    db=os.getenv("POSTGRES_DB", "rewards"),
    port=os.getenv("POSTGRES_PORT", 5432),
)

MODELS_MODULE = ["src.rewards.models", "aerich.models"]

TORTOISE_ORM = {
    "connections": {
        "default": POSTGRES_URL,
    },
    "apps": {
        "models": {"models": MODELS_MODULE, "default_connection": "default"},
    },
}


@dataclass
class Config:
    json_rpc_urls: List[str]
    enodes: Set[str]
    multisender_contract_address: str
    gas_price_wei: int
    private_key: str
    reward_currency: str
    reward_per_percent: float
    reward_min_percent: int
    ping_nodes_interval_munutes: int
    ping_nodes_max_retries: int
    ping_nodes_retries_timeout_secs: int
    rewards_hour: int
    enodes_dir: str
    enodes: Set[str] = field(init=False)
    w3: Web3 = field(init=False)
    multisender_contract: contract = field(init=False)
    address: str = field(init=False)
    rates_url: str
    default_usd_reward_amount: float
    api: RatesAPI = field(init=False)
    xgen_devices_api: str

    def __post_init__(self) -> None:
        enodes_tmp = []
        with contextlib.ExitStack() as stack:
            for filename in glob.glob(os.path.join(self.enodes_dir, "*.txt")):
                enodes_tmp += [
                    line.strip() for line in stack.enter_context(open(filename))
                ]

        self.enodes = set(enodes_tmp)
        self.w3 = Web3(HTTPProvider(self.json_rpc_urls))
        multisender_contract_address_checksum = Web3.toChecksumAddress(
            self.multisender_contract_address
        )
        self.multisender_contract = self.w3.eth.contract(
            address=multisender_contract_address_checksum, abi=MULTISENDER_ABI
        )
        self.address = Account.from_key(self.private_key).address
        self.api = RatesAPI(self.rates_url)


with open(os.path.dirname(__file__) + "/../config.yaml") as f:
    config_data = yaml.safe_load(f)

config: Config = class_schema(Config)().load(config_data)
