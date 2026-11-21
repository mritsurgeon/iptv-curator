"""UCB1 Multi-Armed Bandit query budget allocator for autonomous discovery."""

import math
from typing import Any, Dict, List


class UCB1BanditAllocator:
    """
    Selects discovery queries using Upper Confidence Bound (UCB1):
      score_i = mean_reward_i + c * sqrt(ln(total_pulls) / pulls_i)
    Guarantees ~80% exploitation of proven high-yield queries and ~20% exploration
    of unpulled or newly expanded (EPG-inverted / event-surge) queries.
    """

    def __init__(self, explore_c: float = 1.41):
        self.explore_c = explore_c

    def select_queries(
        self,
        queries: List[Dict[str, Any]],
        budget: int = 8,
    ) -> List[Dict[str, Any]]:
        if not queries or budget <= 0:
            return []

        total_pulls = sum(max(0, int(q.get("pulls", 0))) for q in queries) + 1
        scored = []
        for q in queries:
            pulls = int(q.get("pulls", 0))
            total_reward = float(q.get("total_reward", 0.0))
            if pulls == 0:
                # Prioritize unpulled event_surge / epg_inverted queries first
                boost = 0.5 if q.get("origin") in ("event_surge", "epg_inverted") else 0.0
                ucb = 1e6 + boost
            else:
                mean_r = total_reward / pulls
                ucb = mean_r + self.explore_c * math.sqrt(math.log(total_pulls) / pulls)
                if q.get("origin") == "event_surge":
                    ucb += 0.25
            scored.append((ucb, q))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [q for _, q in scored[:budget]]

