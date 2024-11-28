from pathlib import Path
import json
import boa
from eth_account import Account
import argparse
from decimal import Decimal

from settings.config import BASE_DIR, settings

def format_amount(amount, decimals):
    """Format amount with proper decimals"""
    return Decimal(amount) / Decimal(10 ** decimals)

def get_pool_info(pool_addresses):
    """Get comprehensive pool information for multiple pools"""
    
    # Initialize boa and account
    boa.set_network_env(settings.WEB3_PROVIDER_URL)
    account = Account.from_key(settings.DEPLOYER_EOA_PRIVATE_KEY)
    boa.env.add_account(account)

    pools_data = []
    total_tvl = 0

    for pool_address in pool_addresses:
        try:
            print(f"\nProcessing pool: {pool_address}")
            
            # Load pool contract
            print("Loading pool contract...")
            pool = boa.load_partial(
                Path(BASE_DIR, "contracts", "amm", "stableswap", "implementation", "implementation_v_700.vy")
            ).at(pool_address)

            # Get coin contracts
            print("Loading coin contracts...")
            coin0_address = pool.coins(0)
            coin1_address = pool.coins(1)
            print(f"Coin addresses: {coin0_address}, {coin1_address}")
            
            coin0 = boa.load_partial(Path(BASE_DIR, "tutorial", "contracts", "ERC20mock.vy")).at(coin0_address)
            coin1 = boa.load_partial(Path(BASE_DIR, "tutorial", "contracts", "ERC20mock.vy")).at(coin1_address)

            # Debug each contract call
            print("\nFetching pool data...")
            print("- Getting virtual price...")
            pool_tvl = format_amount(pool.get_virtual_price(), 18)
            total_tvl += float(pool_tvl)
            
            print("- Getting pool name...")
            pool_name = pool.name()
            print("- Getting amplification coefficient...")
            amp_coeff = pool.A()
            print("- Getting pool symbol...")
            pool_symbol = pool.symbol()
            print("- Getting total supply...")
            total_supply = pool.totalSupply()
            
            print("\nFetching coin data...")
            print("- Getting coin0 details...")
            coin0_decimals = coin0.decimals()
            coin0_symbol = coin0.symbol()
            coin0_price = pool.price_oracle(0)
            
            print("- Getting coin1 details...")
            coin1_decimals = coin1.decimals()
            coin1_symbol = coin1.symbol()
            coin1_price = pool.price_oracle(0)

            pool_data = {
                "id": pool_name,
                "address": pool_address,
                "amplificationCoefficient": str(amp_coeff),
                "name": pool_name,
                "symbol": pool_symbol,
                "totalSupply": total_supply,
                "assetTypeName": "unknown",
                "isMetaPool": False,
                "gaugeRewards": [],
                "usdTotal": pool_tvl,
                "gaugeCrvApy": [None, None],
                "implementationAddress": pool_address,
                "implementation": "plainstableng",
                "coins": [
                    {
                        "address": pool.coins(0),
                        "decimals": str(coin0_decimals),
                        "symbol": coin0_symbol,
                        "usdPrice": format_amount(coin0_price, 18)
                    },
                    {
                        "address": pool.coins(1),
                        "decimals": str(coin1_decimals),
                        "symbol": coin1_symbol,
                        "usdPrice": format_amount(coin1_price, 18)
                    }
                ]
            }
            pools_data.append(pool_data)

        except Exception as e:
            print(f"\n❌ Error processing pool {pool_address}")
            print(f"Error details: {str(e)}")
            continue

    # Construct final response
    response = {
        "poolData": pools_data,
        "tvlAll": total_tvl,
        "tvl": total_tvl
    }
    
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get pool information")
    parser.add_argument("--pool_addresses", type=str, nargs='+', required=True, help="List of pool addresses")
    args = parser.parse_args()

    pool_info = get_pool_info(args.pool_addresses)
    
    # Print formatted JSON
    print(json.dumps(pool_info, indent=2, default=str))