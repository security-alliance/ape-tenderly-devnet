import atexit

from ape.api import PluginConfig, UpstreamProvider, Web3Provider, TestProviderAPI, TransactionAPI, ReceiptAPI
from ape.exceptions import ProviderError
from ape.logging import logger
from ape.utils import cached_property
from web3 import HTTPProvider, Web3
from web3.gas_strategies.rpc import rpc_gas_price_strategy
from web3.middleware import geth_poa_middleware
from typing import List, Optional, Union, cast
from eth_typing import (
    HexStr,
)

from ape.types import (
    AddressType,
    SnapshotID,
)

from ape.exceptions import (
    VirtualMachineError,
)
from ape.utils import cached_property
from ethpm_types import HexBytes
from web3 import HTTPProvider, Web3
from web3.gas_strategies.rpc import rpc_gas_price_strategy
from web3.middleware import geth_poa_middleware
from web3.types import TxParams, Wei
from eth_utils import is_0x_prefixed, to_hex

from .client import Fork, TenderlyClient


class TenderlyConfig(PluginConfig):
    auto_remove_forks: bool = True

    host: Optional[str] = None
    """The host address """

    default_gas: Optional[int] = None
    """Default gas fee for transactions"""

    tx_type: Optional[Union[int, HexStr]] = None
    """Default tx type for transactions"""


class TenderlyForkProvider(Web3Provider):
    @cached_property
    def _client(self) -> TenderlyClient:
        return TenderlyClient()

    def _create_fork(self) -> Fork:
        ecosystem_name = self.network.ecosystem.name
        network_name = self.network.name.replace("-fork", "")
        chain_id = self.network.ecosystem.get_network(network_name).chain_id

        logger.debug(f"Creating tenderly fork for '{ecosystem_name}:{network_name}'...")
        fork = self._client.create_fork(chain_id)
        logger.success(f"Created tenderly fork '{fork.id}'.")
        return fork

    @cached_property
    def fork(self) -> Fork:
        # NOTE: Always create a new fork, because the fork will get cached here
        #       per-instance of this class, and "released" when the fork is closed
        return self._create_fork()

    @property
    def uri(self) -> str:
        return self.fork.json_rpc_url

    def connect(self):
        self._web3 = Web3(HTTPProvider(self.uri))
        atexit.register(self.disconnect)  # NOTE: Make sure we de-provision forks

    def disconnect(self):
        if self.config.auto_remove_forks:
            fork_id = self.fork.id
            logger.debug(f"Removing tenderly fork '{fork_id}'...")

            try:
                self._client.remove_fork(fork_id)
                logger.success(f"Removed tenderly fork '{fork_id}'.")

            except Exception as e:
                logger.error(f"Couldn't remove tenderly fork '{fork_id}': {e}.")

        else:
            logger.info(f"Not removing tenderly fork '{self.fork.id}.'")

        self._web3 = None


class TenderlyGatewayProvider(Web3Provider, UpstreamProvider):
    """
    A web3 provider using an HTTP connection to Tenderly's RPC nodes.

    Docs: https://docs.tenderly.co/web3-gateway/web3-gateway
    """

    @cached_property
    def _client(self) -> TenderlyClient:
        return TenderlyClient()

    @property
    def uri(self) -> str:
        return self._client.get_gateway_rpc_uri(self.network.ecosystem.name, self.network.name)

    @property
    def connection_str(self) -> str:
        return self.uri

    def connect(self):
        self._web3 = Web3(HTTPProvider(self.uri))

        try:
            chain_id = self._web3.eth.chain_id
        except Exception as err:
            raise ProviderError(f"Failed to connect to Tenderly Gateway.\n{repr(err)}") from err

        # Any chain that *began* as PoA needs the middleware for pre-merge blocks
        ethereum_goerli = 5
        optimism = (10, 420)
        polygon = (137, 80001)

        if chain_id in (ethereum_goerli, *optimism, *polygon):
            self._web3.middleware_onion.inject(geth_poa_middleware, layer=0)

        self._web3.eth.set_gas_price_strategy(rpc_gas_price_strategy)

    def disconnect(self):
        self._web3 = None


class TenderlyDevnetProvider(Web3Provider, TestProviderAPI):
    """
    A web3 provider using an HTTP connection to Tenderly's Devnet RPC nodes.

    Docs: https://docs.tenderly.co/devnets/intro-to-devnets
    """
    _host: Optional[str] = None
    _default_gas: Optional[int] = None
    _tx_type: Optional[Union[int, HexStr]] = None

    @property
    def unlocked_accounts(self) -> List[AddressType]:
        return list(self.account_manager.test_accounts._impersonated_accounts)

    @cached_property
    def _client(self) -> TenderlyClient:
        return TenderlyClient()

    @property
    def uri(self) -> str:
        if self._host is not None:
            return self._host

        elif config_host := self.settings.host:
            self._host = config_host

        else:
            raise ProviderError(f"Host not provided")

        return self._host

    @property
    def http_uri(self) -> str:
        # NOTE: Overriding `Web3Provider.http_uri` implementation
        return self.uri

    @property
    def connection_str(self) -> str:
        return self.uri

    @property
    def settings(self) -> TenderlyConfig:
        return cast(TenderlyConfig, super().settings)

    def snapshot(self) -> SnapshotID:
        raise NotImplementedError("Tenderly Devnet not added yet")

    def revert(self, snapshot_id: SnapshotID):
        raise NotImplementedError("Tenderly Devnet not added yet")

    def set_timestamp(self, new_timestamp: int):
        self._make_request("evm_setNextBlockTimestamp", [new_timestamp])

    def mine(self, num_blocks: int = 1):
        result = self._make_request("evm_increaseBlocks", [hex(num_blocks)])
        return result
        # TODO handle error

    def connect(self):
        self._web3 = Web3(HTTPProvider(self.uri))

        print(f"Settings: {self.settings}")
        if config_default_gas := self.settings.default_gas:
            print(f"Config default gas: {config_default_gas}")
            self._default_gas = config_default_gas

        if config_tx_type := self.settings.tx_type:
            print(f"Config tx type: {config_tx_type}")
            self._tx_type = config_tx_type

        try:
            chain_id = self._web3.eth.chain_id
        except Exception as err:
            raise ProviderError(f"Failed to connect to Tenderly Devnet.\n{repr(err)}") from err

        # Any chain that *began* as PoA needs the middleware for pre-merge blocks
        ethereum_goerli = 5
        optimism = (10, 420)
        polygon = (137, 80001)

        if chain_id in (ethereum_goerli, *optimism, *polygon):
            self._web3.middleware_onion.inject(geth_poa_middleware, layer=0)

        if (self._default_gas is None):
            self._web3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
        else:
            self._web3.eth.set_gas_price_strategy(lambda web3, txn: Wei(self._default_gas))

    def disconnect(self):
        self._web3 = None

    def unlock_account(self, address: AddressType) -> bool:
        # All accounts can be unlocked
        return True

    def send_transaction(self, txn: TransactionAPI) -> ReceiptAPI:
        """
        Creates a new message call transaction or a contract creation
        for signed transactions.
        """
        sender = txn.sender
        if sender:
            sender = self.conversion_manager.convert(txn.sender, AddressType)

        if sender and sender in self.unlocked_accounts:
            # Allow for an unsigned transaction
            sender = cast(AddressType, sender)  # We know it's checksummed at this point.
            txn = self.prepare_transaction(txn)
            # not supported on tenderly
            # original_code = HexBytes(self.get_code(sender))
            # if original_code:
            #     self.set_code(sender, "")

            txn_dict = txn.dict()
            if isinstance(txn_dict.get("type"), int):
                txn_dict["type"] = HexBytes(txn_dict["type"]).hex()

            tx_params = cast(TxParams, txn_dict)
            # print(f"Tx params: {tx_params}")
            if (self._default_gas is not None):
                print(f"Default gas: {self._default_gas}")
                tx_params["maxFeePerGas"] = self._default_gas
                tx_params["maxPriorityFeePerGas"] = 1000000000

                print(f"Tx type: {self._tx_type}")
                if (self._tx_type is not None):
                    print(f"Tx type set: {self._tx_type}")
                    if (self._tx_type == 0):
                        print(f"Tx type is 0")
                        tx_params.pop("maxFeePerGas", None)
                        tx_params.pop("maxPriorityFeePerGas", None)
                        tx_params["gasPrice"] = self._default_gas
                        tx_params["type"] = "0x0"

            print(f"Tx params after: {tx_params}")

            estimated_gas = self.web3.eth.estimate_gas(tx_params)
            print(f"Estimated gas: {estimated_gas}")
            tx_params["gas"] = int(estimated_gas * 1.2)  # Add buffer

            try:
                txn_hash = self.web3.eth.send_transaction(tx_params)
            except ValueError as err:
                raise self.get_virtual_machine_error(err, txn=txn) from err

            # finally:
            #     if original_code:
            #         self.set_code(sender, original_code.hex())
        else:
            try:
                txn_hash = self.web3.eth.send_raw_transaction(txn.serialize_transaction())
            except ValueError as err:
                vm_err = self.get_virtual_machine_error(err, txn=txn)

                if "nonce too low" in str(vm_err):
                    # Add additional nonce information
                    new_err_msg = f"Nonce '{txn.nonce}' is too low"
                    raise VirtualMachineError(
                        new_err_msg,
                        base_err=vm_err.base_err,
                        code=vm_err.code,
                        txn=txn,
                        source_traceback=vm_err.source_traceback,
                        trace=vm_err.trace,
                        contract_address=vm_err.contract_address,
                    )

                raise vm_err from err

        receipt = self.get_receipt(
            txn_hash.hex(),
            required_confirmations=(
                txn.required_confirmations
                if txn.required_confirmations is not None
                else self.network.required_confirmations
            ),
        )

        if receipt.failed:
            txn_dict = receipt.transaction.dict()
            if isinstance(txn_dict.get("type"), int):
                txn_dict["type"] = HexBytes(txn_dict["type"]).hex()

            txn_params = cast(TxParams, txn_dict)

            # Replay txn to get revert reason
            # NOTE: For some reason, `nonce` can't be in the txn params or else it fails.
            if "nonce" in txn_params:
                del txn_params["nonce"]

            try:
                self.web3.eth.call(txn_params)
            except Exception as err:
                vm_err = self.get_virtual_machine_error(err, txn=receipt)
                raise vm_err from err

        logger.info(f"Confirmed {receipt.txn_hash} (total fees paid = {receipt.total_fees_paid})")
        self.chain_manager.history.append(receipt)
        return receipt

    def get_balance(self, address: str) -> int:
        if hasattr(address, "address"):
            address = address.address

        result = self._make_request("eth_getBalance", [address, "latest"])
        if not result:
            raise ValueError(f"Failed to get balance for : {address}")

        return int(result, 16) if isinstance(result, str) else result

    def set_balance(self, account: AddressType, amount: Union[int, float, str, bytes]):
        is_str = isinstance(amount, str)
        _is_hex = False if not is_str else is_0x_prefixed(str(amount))
        is_key_word = is_str and len(str(amount).split(" ")) > 1
        if is_key_word:
            # This allows values such as "1000 ETH".
            amount = self.conversion_manager.convert(amount, int)
            is_str = False

        amount_hex_str = str(amount)

        # Convert to hex str
        if is_str and not _is_hex:
            amount_hex_str = to_hex(int(amount))
        elif isinstance(amount, int) or isinstance(amount, bytes):
            amount_hex_str = to_hex(amount)

        self._make_request("tenderly_setBalance", [account, amount_hex_str])

    def add_balance(self, account: AddressType, amount: Union[int, float, str, bytes]):
        is_str = isinstance(amount, str)
        _is_hex = False if not is_str else is_0x_prefixed(str(amount))
        is_key_word = is_str and len(str(amount).split(" ")) > 1
        if is_key_word:
            # This allows values such as "1000 ETH".
            amount = self.conversion_manager.convert(amount, int)
            is_str = False

        amount_hex_str = str(amount)

        # Convert to hex str
        if is_str and not _is_hex:
            amount_hex_str = to_hex(int(amount))
        elif isinstance(amount, int) or isinstance(amount, bytes):
            amount_hex_str = to_hex(amount)

        self._make_request("tenderly_addBalance", [account, amount_hex_str])
