from app.policy.engine import evaluate_policy
from app.policy.schemas import PolicyContext, PolicyResult


class PolicyService:
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        return evaluate_policy(context)
