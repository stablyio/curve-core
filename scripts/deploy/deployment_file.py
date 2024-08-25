import re
import time
from enum import StrEnum, auto
from pathlib import Path

import yaml
from boa.contracts.abi.abi_contract import ABIFunction
from boa.contracts.vyper.vyper_contract import VyperContract
from boa.util.abi import abi_encode
from pydantic import BaseModel
from pydantic import ConfigDict as BaseModelConfigDict
from pydantic.v1.utils import deep_update

from scripts.logging_config import get_logger
from settings.config import ChainConfig, RollupType

from .utils import get_latest_commit_hash, get_relative_path

logger = get_logger(__name__)


#  <-------------------------- Chain Config -------------------------->


class DaoSettings(BaseModel):
    crv: str | None = None
    crvusd: str | None = None
    emergency_admin: str | None = None
    ownership_admin: str | None = None
    parameter_admin: str | None = None
    vault: str | None = None


class ChainParameters(BaseModel):
    model_config = BaseModelConfigDict(use_enum_values=True)

    network_name: str
    chain_id: int
    layer: int
    rollup_type: RollupType
    dao: DaoSettings | None = None
    explorer_base_url: str
    native_currency_coingecko_id: str
    native_currency_symbol: str
    platform_coingecko_id: str
    public_rpc_url: str
    wrapped_native_token: str


#  <-------------------------- Contracts -------------------------->


class CompilerSettings(BaseModel):
    compiler_version: str
    evm_version: str | None
    optimisation_level: str


class DeploymentType(StrEnum):
    normal = auto()
    blueprint = auto()


class Contract(BaseModel):
    model_config = BaseModelConfigDict(use_enum_values=True)

    address: str
    compiler_settings: CompilerSettings
    constructor_args_encoded: str | None
    contract_github_url: str
    contract_path: str
    contract_version: str
    deployment_timestamp: int
    deployment_type: DeploymentType


#  <-------------------------- Deployments -------------------------->


class SingleAmmDeployment(BaseModel):
    factory: Contract | None = None
    implementation: Contract | None = None
    math: Contract | None = None
    views: Contract | None = None


class StableswapSingleAmmDeployment(SingleAmmDeployment):
    meta_implementation: Contract | None = None


class AmmDeployment(BaseModel):
    stableswap: StableswapSingleAmmDeployment | None = None
    tricryptoswap: SingleAmmDeployment | None = None
    twocryptoswap: SingleAmmDeployment | None = None


#  <----------------------------------------------------------------->


class GaugeFactoryDeployment(BaseModel):
    factory: Contract | None = None
    implementation: Contract | None = None


class GaugeDeployment(BaseModel):
    child_gauge: GaugeFactoryDeployment


#  <----------------------------------------------------------------->


class GovernanceDeployment(BaseModel):
    agent: Contract | None = None
    relayer: dict[str, Contract] | None = None  # str should be RollupType
    vault: Contract | None = None


#  <----------------------------------------------------------------->


class HelpersDeployment(BaseModel):
    deposit_and_stake_zap: Contract | None = None
    rate_provider: Contract | None = None
    router: Contract | None = None
    stable_swap_meta_zap: Contract | None = None


#  <----------------------------------------------------------------->


class MetaregistryHandlers(BaseModel):
    stableswap: Contract | None = None
    tricryptoswap: Contract | None = None
    twocryptoswap: Contract | None = None


class MetaregistyContract(Contract):
    registry_handlers: MetaregistryHandlers | None = None


class RegistriesDeployment(BaseModel):
    address_provider: Contract | None = None
    metaregistry: MetaregistyContract | None = None


#  <----------------------------------------------------------------->


class ContractsDeployment(BaseModel):
    amm: AmmDeployment | None = None
    gauge: GaugeDeployment | None = None
    governance: GovernanceDeployment | None = None
    helpers: HelpersDeployment | None = None
    registries: RegistriesDeployment | None = None


class DeploymentConfig(BaseModel):
    config: ChainParameters
    contracts: ContractsDeployment | None = None


class YamlDeploymentFile:
    def __init__(self, _file_name: Path):
        self.file_name = _file_name

    def get_deployment_config(self) -> DeploymentConfig | None:
        if not self.file_name.exists():
            return None
        with open(self.file_name, "r") as file:
            deployments = yaml.safe_load(file)

        return DeploymentConfig.model_validate(deployments)

    def get_contract_deployment(self, config_keys: tuple) -> Contract | None:
        """
        Get contract deployment from deployment file if exits

        Args:
        config_keys (list): A list of keys that define contract path
        Returns:
        Contract | None: Contract if exits
        """
        current_level = self.get_deployment_config()
        if current_level is None:
            return None

        for key in config_keys:
            if isinstance(current_level, dict):  # workaround for dict pydantic field
                if key in current_level:
                    current_level = current_level[key]
                else:
                    return None
                continue

            if getattr(current_level, key) is not None:
                current_level = getattr(current_level, key)
            else:
                return None
        return current_level

    def save_deployment_config(self, deployment: DeploymentConfig) -> None:
        with open(self.file_name, "w") as file:
            yaml.safe_dump(deployment.model_dump(), file)

    def update_deployment_config(self, data: dict) -> None:
        """
        Update whole deployment

        Args:
        data (dict): Data of any size for updating deployment (should have same nested values)
        """
        deployment_config = self.get_deployment_config()
        if deployment_config is not None:
            updated_deployment_config = deep_update(deployment_config.model_dump(), data)
        else:
            updated_deployment_config = data

        # Validate data
        DeploymentConfig.model_validate(updated_deployment_config)

        with open(self.file_name, "w") as file:
            yaml.safe_dump(updated_deployment_config, file)

    @staticmethod
    def ensure_nested_dict(d: dict, keys: tuple) -> dict:
        """
        Ensure that a nested dictionary contains the given keys.
        If the keys do not exist, create them.

        Args:
        d (dict): The dictionary to update.
        keys (list): A list of keys that define the nested structure.
        Returns:
        dict: The innermost dictionary that corresponds to the final key in the keys list.
        """
        for key in keys:
            if key not in d or d[key] is None:
                d[key] = {}
            d = d[key]
        return d

    def update_contract_deployment(
        self,
        contract_folder: Path,
        contract_object: VyperContract,
        ctor_args: tuple,
        as_blueprint: bool = False,
    ):
        deployment_config_dict = self.get_deployment_config().model_dump()
        contract_path_keys = contract_folder.parts[contract_folder.parts.index("contracts") :]

        # fill nested keys if they don't exist and return the innermost nest based on contract_folder:
        contract_deployment = self.ensure_nested_dict(deployment_config_dict, contract_path_keys)

        # get abi-encoded ctor args:
        if ctor_args:
            ctor_abi_object = ABIFunction(
                next(i for i in contract_object.abi if i["type"] == "constructor"), contract_name="ctor_abi"
            )
            abi_args = ctor_abi_object._merge_kwargs(*ctor_args)
            encoded_args = abi_encode(ctor_abi_object.signature, abi_args).hex()
        else:
            encoded_args = None

        # fetch data from contract pragma:
        pattern = r"# pragma version ([\d.]+)"
        match = re.search(pattern, contract_object.compiler_data.source_code)
        if match:
            compiler_version = match.group(1)
        else:
            raise ValueError("Compiler Version is set incorrectly")

        # latest git commit hash:
        latest_git_commit_for_file = get_latest_commit_hash(contract_object.filename)
        contract_relative_path = get_relative_path(contract_object.filename)
        github_url = (
            f"https://github.com/curvefi/curve-lite/blob/{latest_git_commit_for_file}/"
            f"{'/'.join(contract_relative_path.parts[1:])}"
        )

        if not as_blueprint:
            version = contract_object.version().strip()
        else:
            pattern = 'version: public\(constant\(String\[8\]\)\) = "([\d.]+)"'
            match = re.search(pattern, contract_object.compiler_data.source_code)

            if match:
                version = match.group(1)
            else:
                raise ValueError("Contract version is set incorrectly")

        # store contract deployment metadata:
        contract_deployment.update(
            {
                "deployment_type": "normal" if not as_blueprint else "blueprint",
                "contract_version": version,
                "contract_path": str(contract_relative_path),
                "contract_github_url": github_url,
                "address": contract_object.address.strip(),
                "deployment_timestamp": int(time.time()),
                "constructor_args_encoded": encoded_args,
                "compiler_settings": {
                    "compiler_version": compiler_version,
                    "optimisation_level": contract_object.compiler_data.settings.optimize._name_,
                    "evm_version": contract_object.compiler_data.settings.evm_version,
                },
            }
        )

        self.save_deployment_config(DeploymentConfig.model_validate(deployment_config_dict))

    def dump_initial_chain_settings(self, chain_settings: ChainConfig):
        update_parameters = {
            "config": {
                **chain_settings.model_dump(exclude_none=True),
            }
        }
        self.update_deployment_config(update_parameters)
