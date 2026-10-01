from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from decimal import Decimal
from typing import Any

from app.config import settings
from app.drunix.exceptions import DrunixClientError, DrunixConflictError


class DrunixPaymentClient:
    def __init__(
        self,
        network_path: str | Path | None = None,
        channel: str | None = None,
        chaincode: str | None = None,
    ) -> None:
        self.network_path = Path(network_path or settings.DRUNIX_NETWORK_PATH).expanduser()
        self.channel = channel or settings.DRUNIX_CHANNEL
        self.chaincode = chaincode or settings.DRUNIX_CHAINCODE
        self.enabled = str(settings.DRUNIX_MODE).lower() == "real" and self._network_exists()

    @property
    def network_script(self) -> Path:
        return self.network_path / "network.sh"

    @staticmethod
    def _to_wsl_path(path: str | Path) -> str:
        raw = str(path).replace("\\", "/")
        if raw.startswith("/"):
            return raw
        if len(raw) >= 2 and raw[1] == ":":
            drive = raw[0].lower()
            remainder = raw[2:]
            return f"/mnt/{drive}{remainder}"
        return raw

    def _network_exists(self) -> bool:
        if self.network_script.exists():
            return True
        wsl_path = Path(self._to_wsl_path(self.network_path))
        return wsl_path.exists() and (wsl_path / "network.sh").exists()

    def _bash_network_path(self) -> str:
        return self._to_wsl_path(self.network_path)

    def _bash_peer_cli_path(self) -> str:
        return self._to_wsl_path(self.network_path.parent / "bin")

    def _run_bash(self, command: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="trustpay-drunix-") as temp_dir:
            host_log_path = Path(temp_dir) / "command.log"
            environment = os.environ.copy()
            environment["DRUNIX_LOG_FILE"] = self._to_wsl_path(host_log_path)
            shell_command = (
                f'export PATH={shlex.quote(self._bash_peer_cli_path())}:"$PATH" && '
                f'export DRUNIX_LOG_FILE={shlex.quote(environment["DRUNIX_LOG_FILE"])} && {command}'
            )
            return subprocess.run(
                ["bash", "-lc", shell_command],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

    def is_enabled(self) -> bool:
        return self.enabled

    @staticmethod
    def _contract_sequence_for(policy_decision: str) -> list[str]:
        sequence_by_decision = {
            "APPROVE": ["CreatePayment", "AssessRisk", "ApprovePayment", "CompletePayment"],
            "VERIFY": ["CreatePayment", "AssessRisk", "RequestVerification"],
            "HOLD": ["CreatePayment", "AssessRisk", "HoldPayment"],
            "REJECT": ["CreatePayment", "AssessRisk", "RejectPayment"],
        }
        try:
            return list(sequence_by_decision[policy_decision])
        except KeyError as exc:
            raise ValueError(f"Unsupported policy decision: {policy_decision}") from exc

    @staticmethod
    def _minor_units(amount: Decimal | str | int | float) -> int:
        value = Decimal(str(amount)) * 100
        if value != value.to_integral_value():
            raise DrunixClientError("Payment amount cannot be represented in minor currency units")
        return int(value)

    @staticmethod
    def _raise_invoke_error(function_name: str, message: str) -> None:
        normalized = message.upper()
        if (
            "MVCC" in normalized
            or "PHANTOM_READ_CONFLICT" in normalized
            or "PHANTOM READ CONFLICT" in normalized
            or "INSUFFICIENT LEDGER BALANCE" in normalized
        ):
            raise DrunixConflictError(f"DRUNIX rejected {function_name} because ledger state changed concurrently: {message}")
        raise DrunixClientError(f"DRUNIX invoke failed for {function_name}: {message}")

    @staticmethod
    def _command_output(result: subprocess.CompletedProcess[str]) -> str:
        return "\n".join(output.strip() for output in (result.stdout, result.stderr) if output.strip())

    @staticmethod
    def _decode_response(output: str) -> Any:
        text = output.strip()
        if not text:
            return {}
        decoder = json.JSONDecoder()
        best_match: tuple[int, Any] | None = None
        for start, character in enumerate(text):
            if character not in "[{":
                continue
            try:
                response, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                continue
            consumed = end - start
            if best_match is None or consumed > best_match[0]:
                best_match = (consumed, response)
        return best_match[1] if best_match is not None else {"raw": text}

    def _invoke(self, function_name: str, args: list[str]) -> Any:
        if not self.enabled:
            return None
        payload = {"Args": [function_name, *args]}
        command = (
            "cd {path} && DELAY=3 ./network.sh cc invoke -c {channel} -ccn {chaincode} "
            "-ccic {payload}"
        ).format(
            path=shlex.quote(self._bash_network_path()),
            channel=shlex.quote(self.channel),
            chaincode=shlex.quote(self.chaincode),
            payload=shlex.quote(json.dumps(payload, separators=(",", ":"))),
        )
        result = self._run_bash(command)
        if result.returncode != 0:
            self._raise_invoke_error(function_name, self._command_output(result))
        response = self._decode_response(result.stdout)
        if isinstance(response, dict) and response.get("status") == "ERROR":
            raise DrunixClientError(f"DRUNIX invoke returned an error for {function_name}: {response}")
        return response

    def _query(self, function_name: str, args: list[str]) -> Any:
        if not self.enabled:
            return None
        payload = {"Args": [function_name, *args]}
        command = (
            "cd {path} && DELAY=3 ./network.sh cc query -c {channel} -ccn {chaincode} "
            "-ccqc {payload}"
        ).format(
            path=shlex.quote(self._bash_network_path()),
            channel=shlex.quote(self.channel),
            chaincode=shlex.quote(self.chaincode),
            payload=shlex.quote(json.dumps(payload, separators=(",", ":"))),
        )
        result = self._run_bash(command)
        if result.returncode != 0:
            raise DrunixClientError(
                f"DRUNIX query failed for {function_name}: {self._command_output(result)}"
            )
        response = self._decode_response(result.stdout)
        if isinstance(response, dict) and response.get("status") == "ERROR":
            raise DrunixClientError(f"DRUNIX query returned an error for {function_name}: {response}")
        return response

    def submit_payment(
        self,
        transaction_id: str,
        sender_user_id: str,
        receiver_user_id: str,
        sender_account_id: str,
        receiver_account_id: str,
        amount: Decimal | int | float | str,
        sender_balance: Decimal | int | float | str,
        receiver_balance: Decimal | int | float | str,
        currency: str,
        risk_score: float,
        risk_level: str,
        policy_decision: str,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        args = [
            transaction_id,
            sender_user_id,
            receiver_user_id,
            sender_account_id,
            receiver_account_id,
            str(self._minor_units(amount)),
            str(self._minor_units(sender_balance)),
            str(self._minor_units(receiver_balance)),
            currency,
            str(float(risk_score)),
            risk_level,
            policy_decision,
        ]
        invoke_error: DrunixClientError | None = None
        try:
            self._invoke("SubmitPayment", args)
        except DrunixConflictError:
            raise
        except DrunixClientError as exc:
            invoke_error = exc
        latest = self.query_payment(transaction_id)
        if invoke_error is not None and not self._matches_submitted_payment(
            latest,
            transaction_id,
            sender_user_id,
            receiver_user_id,
            sender_account_id,
            receiver_account_id,
            self._minor_units(amount),
            currency,
            float(risk_score),
            risk_level,
            policy_decision,
        ):
            raise invoke_error
        sender = self.query_account(sender_account_id)
        receiver = self.query_account(receiver_account_id)
        return {
            "status": "committed",
            "transaction_id": transaction_id,
            "data": latest,
            "accounts": {"sender": sender, "receiver": receiver},
            "calls": ["SubmitPayment"],
        }

    def query_payment(self, transaction_id: str) -> dict[str, Any]:
        return self._query("GetPayment", [transaction_id])

    def query_account(self, account_id: str) -> dict[str, Any]:
        return self._query("GetAccount", [account_id])

    def query_payment_history(self, transaction_id: str) -> list[Any]:
        result = self._query("GetPaymentHistory", [transaction_id])
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("data", [])
        return []

    def _matches_submitted_payment(
        self,
        payment: Any,
        transaction_id: str,
        sender_user_id: str,
        receiver_user_id: str,
        sender_account_id: str,
        receiver_account_id: str,
        amount_minor: int,
        currency: str,
        risk_score: float,
        risk_level: str,
        policy_decision: str,
    ) -> bool:
        if not isinstance(payment, dict):
            return False
        expected = {
            "transactionId": transaction_id,
            "senderId": sender_user_id,
            "receiverId": receiver_user_id,
            "senderAccountId": sender_account_id,
            "receiverAccountId": receiver_account_id,
            "amountMinor": amount_minor,
            "currency": currency,
            "riskScore": risk_score,
            "riskLevel": risk_level,
            "policyDecision": policy_decision,
        }
        return (
            all(payment.get(key) == value for key, value in expected.items())
            and payment.get("status") in {"COMPLETED", "VERIFICATION_REQUIRED", "HELD", "REJECTED"}
        )

    def sync_payment(
        self,
        payment: Any,
        risk_result: Any,
        policy_result: Any,
        sender_account: Any,
        receiver_account: Any,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        return self.submit_payment(
            transaction_id=payment.transaction_id,
            sender_user_id=str(payment.sender_user_id),
            receiver_user_id=str(payment.receiver_user_id),
            sender_account_id=str(sender_account.id),
            receiver_account_id=str(receiver_account.id),
            amount=payment.amount,
            sender_balance=sender_account.balance,
            receiver_balance=receiver_account.balance,
            currency=payment.currency,
            risk_score=float(risk_result.risk_score),
            risk_level=risk_result.risk_level,
            policy_decision=policy_result.decision,
        )
