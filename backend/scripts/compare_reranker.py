"""Compare retrieval quality with the reranker disabled vs enabled.

Runs the live hybrid pipeline (Qdrant + PostgreSQL FTS) for a fixed set of
questions over the currently uploaded documents and reports, for each
question, the rank at which the expected document shows up plus overall
MRR@5 and Recall@5.

Usage (mode is controlled by CMP_MODE=on|off):
    docker compose exec -e CMP_MODE=off backend python scripts/compare_reranker.py
    docker compose exec -e CMP_MODE=on  backend python scripts/compare_reranker.py
"""

import os

from app.core.config import settings

settings.RERANKER_ENABLED = os.environ.get("CMP_MODE", "off").lower() == "on"

from app.services import retrieval  # noqa: E402

# question -> original filename expected to be the relevant document
QUESTIONS = [
    ("какой бюджет у проекта Атлант", "atlant.txt"),
    ("сколько денег на рекламу квадрокоптеров", "quad_budget.txt"),
    ("рабочая температура криостата XQ-77 в милликельвинах", "cryostat.txt"),
    ("что перевозят грузовики в температурных контейнерах", "transport.txt"),
    ("какова температура криостата, число милликельвин", "cryostat.txt"),
    ("план продаж квадрокоптера KQ-7000", "quad_budget2.txt"),
    ("дедлайн запуска прототипа проекта", "atlant.txt"),
    ("сколько времени готовить салат из кукурузы", "recipes.txt"),
]

TOP_K = 5


def mrr(ranks: list[int | None]) -> float:
    hits = [1.0 / r for r in ranks if r]
    return sum(hits) / len(ranks) if ranks else 0.0


def main() -> None:
    mode = "RERANK ON " if settings.RERANKER_ENABLED else "RERANK OFF"
    print(f"=== {mode} ===")
    ranks: list[int | None] = []
    for question, expected in QUESTIONS:
        chunks = retrieval.retrieve_context(
            question=question, user_id=1, top_k=TOP_K, min_score=0.3
        )
        order = []
        rank = None
        for pos, chunk in enumerate(chunks, start=1):
            name = chunk.source.filename
            order.append(f"#{pos}{name}({chunk.score:.3f})")
            if name == expected and rank is None:
                rank = pos
        ranks.append(rank)
        print(f"- {question[:45]:47} expected={expected:16} rank={rank if rank else '-':>4}")
        if order:
            print(f"    top{len(chunks)}: " + " ".join(order))
        else:
            print("   (no results)")
    recall = sum(1 for r in ranks if r) / len(ranks)
    print(f"MRR@5 = {mrr(ranks):.3f}   Recall@5 = {recall:.3f}")


if __name__ == "__main__":
    main()