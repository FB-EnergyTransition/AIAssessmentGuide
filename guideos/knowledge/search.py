import math
import re
from collections import Counter


NGRAM = 4
BM25_K1 = 1.5
BM25_B = 0.75


def terms(text: str) -> list[str]:
    """Search terms of a text: short words as they are, longer words as
    overlapping character n-grams, so German compounds and inflections
    (Heizungstausch / Tausch der Heizung) still share terms."""
    result = []
    for word in re.findall(r"\w+", text.casefold()):
        if len(word) <= NGRAM:
            result.append(word)
        else:
            result.extend(word[index:index + NGRAM] for index in range(len(word) - NGRAM + 1))
    return result


def lexical_scores(query: str, texts: list[str]) -> list[float]:
    """BM25 score of every text for the query; 0.0 means no shared term."""
    counts = [Counter(terms(text)) for text in texts]
    lengths = [sum(count.values()) for count in counts]
    average = (sum(lengths) / len(lengths)) if lengths else 0
    scores = [0.0] * len(texts)
    for term in set(terms(query)):
        matching = sum(1 for count in counts if term in count)
        if matching == 0:
            continue
        idf = math.log(1 + (len(texts) - matching + 0.5) / (matching + 0.5))
        for index, count in enumerate(counts):
            frequency = count.get(term, 0)
            if frequency:
                norm = 1 - BM25_B + BM25_B * lengths[index] / average
                scores[index] += idf * frequency * (BM25_K1 + 1) / (frequency + BM25_K1 * norm)
    return scores


def cosine_scores(query: list[float], vectors: list[list[float]]) -> list[float]:
    query_norm = math.sqrt(math.sumprod(query, query))
    scores = []
    for vector in vectors:
        norm = math.sqrt(math.sumprod(vector, vector))
        if not query_norm or not norm or len(vector) != len(query):
            scores.append(0.0)
        else:
            scores.append(math.sumprod(query, vector) / (query_norm * norm))
    return scores


RRF_K = 60


def reciprocal_rank_fusion(rankings: list[list], k: int = RRF_K) -> list[tuple]:
    """Fuse several rankings (best first) into one: every item scores the
    sum of 1 / (k + rank) over the lists it appears in, so items ranked
    well by both the lexical and the semantic search come first."""
    scores: dict = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
