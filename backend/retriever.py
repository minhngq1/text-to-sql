import json
import logging
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

_STOPWORDS_RAW = {
    "các", "của", "là", "có", "cho", "tôi", "những", "theo", "và", "một",
    "trong", "nào", "với", "ở", "thì", "này", "đó", "ra", "hãy", "cái",
}


def _normalize(text):
    text = text.lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


_STOPWORDS = {_normalize(word) for word in _STOPWORDS_RAW}


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", _normalize(text))
    return [w for w in words if len(w) > 1 and w not in _STOPWORDS]


# Finds the most similar examples to the question using TF-IDF and cosine similarity
class ExampleRetriever:
    MIN_RATIO = 0.25

    def __init__(self, path):
        self.examples = self._load(path)
        docs = [_tokens(e["question"]) for e in self.examples]

        df = Counter()
        for doc in docs:
            df.update(set(doc))
        n = len(docs) or 1
        self._idf = {tok: math.log((n + 1) / (c + 1)) + 1 for tok, c in df.items()}
        self._vectors = [self._vector(doc) for doc in docs]

    # Loads the example bank from a JSON file
    @staticmethod
    def _load(path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error("Không đọc được ngân hàng ví dụ %s: %s", path, e)
            return []
        if not isinstance(data, list):
            logger.error("Ngân hàng ví dụ %s phải là một danh sách.", path)
            return []
        valid = [e for e in data if isinstance(e, dict) and e.get("question") and e.get("sql")]
        if len(valid) != len(data):
            logger.warning("Bỏ qua %s ví dụ thiếu question hoặc sql.", len(data) - len(valid))
        return valid

    # Length-normalized TF-IDF vector
    def _vector(self, tokens):
        if not tokens:
            return {}
        counts = Counter(tokens)
        vec = {t: (c / len(tokens)) * self._idf.get(t, 1.0) for t, c in counts.items()}
        length = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / length for t, v in vec.items()}

    # Retrieves up to k examples most similar to the question
    def retrieve(self, question, k=3):
        query = self._vector(_tokens(question))
        if not query:
            return []

        scored = []
        for ex, vec in zip(self.examples, self._vectors):
            score = sum(weight * vec.get(tok, 0.0) for tok, weight in query.items())
            if score > 0:
                scored.append((score, ex))
        if not scored:
            return []

        scored.sort(key=lambda pair: pair[0], reverse=True)
        cutoff = scored[0][0] * self.MIN_RATIO
        return [ex for score, ex in scored[:k] if score >= cutoff]
