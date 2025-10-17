import json

with open("contracts/multisender_abi.json") as f:
    MULTISENDER_ABI = json.load(f)

with open("contracts/erc20_abi.json") as f:
    ERC20_ABI = json.load(f)
