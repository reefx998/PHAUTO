"""
Pharos Testnet Airdrop Bot – interactive menu
============================================
Automates transfers, swaps and add‑liquidity operations on the Pharos
Testnet for the purpose of airdrop farming.

Highlights
----------
* Interactive terminal prompts – choose DEX, how many transfers / swaps /
  liquidity‑adds, delay range and token amounts at runtime.
* Reads a 0x‑prefixed private key from either the environment variable
  `PRIVATE_KEY` or the file `privatekey.txt` (first non‑empty line).
* Compatible with both `web3.py` v5 and v6. The script first attempts to
  import `geth_poa_middleware` from the new v6 location and falls back to
  the old v5 location if necessary.

Quick start
-----------
1. Install requirements:

       pip install "web3<6" eth_account python-dotenv

   If you prefer web3 v6, simply install `web3` without the version pin –
   the script will still work.

2. Save your Pharos testnet private key:

       echo 0xYOUR_PRIVATE_KEY > privatekey.txt

   or export it in the current shell:

       export PRIVATE_KEY=0xYOUR_PRIVATE_KEY

3. Run the bot and follow the prompts:

       python pharos_airdrop_bot.py
"""
import os
import sys
import time
import random
import secrets
from typing import Tuple

from web3 import Web3

# geth_poa_middleware is located in a different module beginning with web3 v6
try:
    from web3.middleware.poa import geth_poa_middleware  # web3 v6+
except ImportError:
    from web3.middleware import geth_poa_middleware      # web3 v5

from eth_account import Account

###############################################################################
# Network configuration                                                       #
###############################################################################
PHAROS_RPC = os.getenv("PHAROS_RPC", "https://testnet.dplabs-internal.com")
CHAIN_ID   = 688_688  # Pharos testnet chain‑id as per docs 2025‑06‑20

# Tokens (addresses current as of 2025‑07‑09)
PHRS  = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"  # native pseudo‑address
WPHRS = "0x3019B247381c850ab53Dc0EE53bCe7A07Ea9155f"
USDC  = "0x72df0bcd7276f2dFbAc900D1CE63c272C4BCcCED"
USDT  = "0xD4071393f8716661958F766DF660033b3d35fD29"

TOKENS = {"PHRS": PHRS, "WPHRS": WPHRS, "USDC": USDC, "USDT": USDT}
DEC    = {"PHRS": 18,   "WPHRS": 18,   "USDC": 6,    "USDT": 6}

# Routers (Uniswap‑V2 style). Replace the Zenith router when announced.
ROUTERS = {
    "faroswap":   "0x3541423f25A1Ca5C98fdBCf478405d3f0aaD1164",
    "zenithswap": "0xYourZenithRouterAddressHere",
}

###############################################################################
# Minimal ABIs                                                                #
###############################################################################
UNISWAP_ROUTER_ABI = [
    {
        "name": "swapExactTokensForTokens",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "amountIn",     "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path",         "type": "address[]"},
            {"name": "to",           "type": "address"},
            {"name": "deadline",     "type": "uint256"}
        ],
        "outputs": [ {"name": "amounts", "type": "uint256[]"} ]
    },
    {
        "name": "addLiquidity",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenA",       "type": "address"},
            {"name": "tokenB",       "type": "address"},
            {"name": "amountADesired","type": "uint256"},
            {"name": "amountBDesired","type": "uint256"},
            {"name": "amountAMin",   "type": "uint256"},
            {"name": "amountBMin",   "type": "uint256"},
            {"name": "to",           "type": "address"},
            {"name": "deadline",     "type": "uint256"}
        ],
        "outputs": [
            {"name": "amountA",   "type": "uint256"},
            {"name": "amountB",   "type": "uint256"},
            {"name": "liquidity", "type": "uint256"}
        ]
    }
]

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [ {"type": "address"} ], "outputs": [ {"type": "uint256"} ]},
    {"name": "approve",   "type": "function", "stateMutability": "nonpayable", "inputs": [ {"name": "spender", "type": "address"},{"name": "amount",  "type": "uint256"} ], "outputs": [ {"type": "bool"} ]},
    {"name": "allowance", "type": "function", "stateMutability": "view", "inputs": [ {"name": "owner",   "type": "address"},{"name": "spender", "type": "address"} ], "outputs": [ {"type": "uint256"} ]},
]

###############################################################################
# Utility helpers                                                             #
###############################################################################

def load_private_key() -> str:
    """Return a 0x‑prefixed private key from env var or privatekey.txt."""
    key = os.getenv("PRIVATE_KEY", "").strip()
    if not key and os.path.isfile("privatekey.txt"):
        with open("privatekey.txt", "r", encoding="utf-8") as fp:
            for line in fp:
                if line.strip():
                    key = line.strip()
                    break
    if key.startswith("0x") and len(key) == 66:
        return key
    sys.exit("No valid 0x‑prefixed private key found (env or privatekey.txt)")


def w3_connect(rpc: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        sys.exit("Cannot connect to RPC endpoint – check URL and connection")
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    return w3


def checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def build_tx(w3: Web3, acct: Account, tx: dict) -> str:
    tx.setdefault("chainId", CHAIN_ID)
    tx.setdefault("nonce", w3.eth.get_transaction_count(acct.address))
    gas_price = w3.to_wei(1, "gwei")  # generous for testnet
    tx.setdefault("maxPriorityFeePerGas", gas_price)
    tx.setdefault("maxFeePerGas", gas_price)
    signed = acct.sign_transaction(tx)
    return w3.eth.send_raw_transaction(signed.rawTransaction).hex()


def wait_receipt(w3: Web3, tx_hash: str):
    rc = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=240)
    print("Mined in block", rc.blockNumber)
    return rc


def approve_if_needed(w3: Web3, acct: Account, token: str, spender: str, amount: int):
    if token == PHRS:
        return  # native token – no approve required
    erc20 = w3.eth.contract(checksum(token), abi=ERC20_ABI)
    allowance = erc20.functions.allowance(acct.address, spender).call()
    if allowance < amount:
        tx = erc20.functions.approve(spender, 2**256 - 1).build_transaction({"from": acct.address, "gas": 80000})
        tx_hash = build_tx(w3, acct, tx)
        wait_receipt(w3, tx_hash)

###############################################################################
# Actions                                                                     #
###############################################################################

def random_address() -> str:
    return Account.from_key(secrets.token_hex(32)).address


def do_transfer(w3: Web3, acct: Account, value_eth: float):
    destination = random_address()
    tx_hash = build_tx(w3, acct, {"to": destination, "value": w3.to_wei(value_eth, "ether"), "gas": 21000})
    wait_receipt(w3, tx_hash)
    print(f"Transferred {value_eth} PHRS to {destination[:10]}…")


def do_swap(w3: Web3, acct: Account, router_addr: str, amount: float):
    router = w3.eth.contract(checksum(router_addr), abi=UNISWAP_ROUTER_ABI)
    token_in, token_out = random.sample(list(TOKENS.values()), 2)
    symbol_in = next(s for s, a in TOKENS.items() if a == token_in)
    decimals_in = DEC[symbol_in]
    amount_in = int(amount * 10 ** decimals_in)
    approve_if_needed(w3, acct, token_in, router.address, amount_in)
    deadline = int(time.time()) + 120
    tx = router.functions.swapExactTokensForTokens(
        amount_in,
        0,
        [checksum(token_in), checksum(token_out)],
        acct.address,
        deadline
    ).build_transaction({"from": acct.address, "gas": 300000})
    tx_hash = build_tx(w3, acct, tx)
    wait_receipt(w3, tx_hash)
    print(f"Swapped {amount} {symbol_in} via {router_addr[:10]}…")


def do_add_liquidity(w3: Web3, acct: Account, router_addr: str, amount: float):
    router = w3.eth.contract(checksum(router_addr), abi=UNISWAP_ROUTER_ABI)
    tokenA, tokenB = random.sample(list(TOKENS.values()), 2)
    symbolA = next(s for s, a in TOKENS.items() if a == tokenA)
    symbolB = next(s for s, a in TOKENS.items() if a == tokenB)
    amountA = int(amount * 10 ** DEC[symbolA])
    amountB = int(amount * 10 ** DEC[symbolB])
    approve_if_needed(w3, acct, tokenA, router.address, amountA)
    approve_if_needed(w3, acct, tokenB, router.address, amountB)
    deadline = int(time.time()) + 120
    tx = router.functions.addLiquidity(
        checksum(tokenA),
        checksum(tokenB),
        amountA,
        amountB,
        1,
        1,
        acct.address,
        deadline
    ).build_transaction({"from": acct.address, "gas": 400000})
    tx_hash = build_tx(w3, acct, tx)
    wait_receipt(w3, tx_hash)
    print(f"Added LP {symbolA}‑{symbolB} with {amount} each via {router_addr[:10]}…")


###############################################################################
# Main menu                                                                   #
###############################################################################

def main():
    print("== Pharos Testnet AutoBot ==")
    print("[1] Faroswap")
    print("[2] ZenithSwap")
    dex_option = input("Select DEX [1/2]: ").strip()
    router_key = "faroswap" if dex_option == "1" else "zenithswap"
    router_addr = ROUTERS[router_key]

    n_tx = int(input("Number of transfers to run: ").strip())
    n_swaps = int(input("Number of swaps to run: ").strip())
    n_lp = int(input("Number of LP adds to run: ").strip())
    phrs_amt = float(input("PHRS per transfer (e.g. 0.001): ").strip())
    token_amt = float(input("Token amount per swap/LP (e.g. 1): ").strip())
    delay = float(input("Delay (seconds) between actions: ").strip())

    w3 = w3_connect(PHAROS_RPC)
    key = load_private_key()
    acct = Account.from_key(key)
    print("Using address:", acct.address)

    for _ in range(n_tx):
        do_transfer(w3, acct, phrs_amt)
        time.sleep(delay)

    for _ in range(n_swaps):
        do_swap(w3, acct, router_addr, token_amt)
        time.sleep(delay)

    for _ in range(n_lp):
        do_add_liquidity(w3, acct, router_addr, token_amt)
        time.sleep(delay)

    print("All done.")

if __name__ == "__main__":
    main()

