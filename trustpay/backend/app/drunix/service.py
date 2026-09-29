from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.config import settings
from app.drunix.exceptions import DrunixClientError
from app.drunix.schemas import DrunixInvocationResult


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
        shell_command = f'export PATH="{self._bash_peer_cli_path()}:$PATH" && {command}'
        return subprocess.run(["bash", "-lc", shell_command], capture_output=True, text=True, check=False)

    def is_enabled(self) -> bool:
        return self.enabled

    def _contract_sequence_for(self, decision: str) -> list[str]:
        normalized = decision.strip().upper()
        mapping = {
            "APPROVE": ["CreatePayment", "AssessRisk", "ApprovePayment", "CompletePayment"],
            "VERIFY": ["CreatePayment", "AssessRisk", "RequestVerification"],
            "HOLD": ["CreatePayment", "AssessRisk", "HoldPayment"],
            "REJECT": ["CreatePayment", "AssessRisk", "RejectPayment"],
        }
        return mapping.get(normalized, ["CreatePayment", "AssessRisk", "RejectPayment"])

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
            'cd "{path}" && DELAY=3 ./network.sh cc invoke -c {channel} -ccn {chaincode} '
            "-ccic '{payload}'"
        ).format(
            path=self._bash_network_path(),
            channel=self.channel,
            chaincode=self.chaincode,
            payload=json.dumps(payload, separators=(",", ":")),
        )
        result = self._run_bash(command)
        if result.returncode != 0:
            raise DrunixClientError(
                f"DRUNIX invoke failed for {function_name}: {result.stderr.strip() or result.stdout.strip()}"
            )
        response = self._decode_response(result.stdout)
        if isinstance(response, dict) and response.get("status") == "ERROR":
            raise DrunixClientError(f"DRUNIX invoke returned an error for {function_name}: {response}")
        return response

    def _query(self, function_name: str, args: list[str]) -> Any:
        if not self.enabled:
            return None
        payload = {"Args": [function_name, *args]}
        command = (
            'cd "{path}" && DELAY=3 ./network.sh cc query -c {channel} -ccn {chaincode} '
            "-ccqc '{payload}'"
        ).format(
            path=self._bash_network_path(),
            channel=self.channel,
            chaincode=self.chaincode,
            payload=json.dumps(payload, separators=(",", ":")),
        )
        result = self._run_bash(command)
        if result.returncode != 0:
            raise DrunixClientError(
                f"DRUNIX query failed for {function_name}: {result.stderr.strip() or result.stdout.strip()}"
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
        amount: int | float | str,
        currency: str,
        risk_score: float,
        risk_level: str,
        policy_decision: str,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        normalized_amount = int(amount)
        sequence = self._contract_sequence_for(policy_decision)
        call_chain: list[tuple[str, list[str]]] = [
            ("CreatePayment", [transaction_id, sender_user_id, receiver_user_id, str(normalized_amount), currency]),
            ("AssessRisk", [transaction_id, str(float(risk_score)), risk_level, policy_decision]),
        ]
        for function_name in sequence[2:]:
            call_chain.append((function_name, [transaction_id]))

        results: list[DrunixInvocationResult] = []
        for function_name, args in call_chain:
            response = self._invoke(function_name, args)
            results.append(DrunixInvocationResult(function=function_name, arguments=tuple(args), response=response))

        latest = self.query_payment(transaction_id)
        return {"status": "synced", "transaction_id": transaction_id, "data": latest, "calls": [result.function for result in results]}

    def query_payment(self, transaction_id: str) -> dict[str, Any]:
        return self._query("GetPayment", [transaction_id])

    def query_payment_history(self, transaction_id: str) -> list[Any]:
        result = self._query("GetPaymentHistory", [transaction_id])
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("data", [])
        return []

    def sync_payment(self, payment: Any, risk_result: Any, policy_result: Any) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        return self.submit_payment(
            transaction_id=payment.transaction_id,
            sender_user_id=str(payment.sender_user_id),
            receiver_user_id=str(payment.receiver_user_id),
            amount=int(payment.amount),
            currency=payment.currency,
            risk_score=float(risk_result.risk_score),
            risk_level=risk_result.risk_level,
            policy_decision=policy_result.decision,
        )
