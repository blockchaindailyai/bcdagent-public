"""Exercise actual policy/plan logic with synthetic inputs and no execution adapter."""
import json
from dataclasses import asdict
from openclaw_agent.capabilities.governance import GovernanceAction, route_governance_lane
from openclaw_agent.capabilities.policy_controls import DecisionIntent, evaluate_intent, reconciliation_status

def main():
    action = GovernanceAction('synthetic-001', 'rebalance', 2.0, True)
    intent = DecisionIntent('synthetic-001', 'TRX', 'borrow', 2.0, 2.0, 'jTRX', 'TRX')
    print(json.dumps({'mode':'offline-example', 'governance':asdict(route_governance_lane(action)),
                      'policy':asdict(evaluate_intent(intent)),
                      'reconciliation':reconciliation_status(0.0, 0), 'broadcast':False},indent=2))

if __name__=='__main__':main()
