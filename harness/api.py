"""外の API（Jev と、OpenRouter の LLM）。標準ライブラリだけで呼ぶ（urllib をスレッドで回す）。

- Jev は確率で答える。**失敗した問いは答えに入らない（棄権）。偽ではない**
- LLM は文章を書くだけ。判断はさせない
テストでは、同じ形の偽物（tests/fakes.py）に差し替える。
"""
import asyncio
import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("utsuwa")

JEV_URL = "https://api.typesafe.ai/v1/systemone"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"


def noul(instructions):
    """はい／いいえの確率を聞く問い。"""
    return {"type": "noul", "instructions": instructions}


def choice(instructions, criteria):
    """決まった選択肢から1つを選ぶ問い。criteria は {選択肢: 説明}。"""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def _post(url, body, key, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Jev:
    def __init__(self, key, model="jev-latest", timeout=15):
        self.key, self.model, self.timeout = key, model, timeout

    async def ask(self, state, questions):
        """questions = {名前: noul(...) か choice(...)}。答えは {名前: 確率} か {名前: (選んだもの, {選択肢: 確率})}。"""
        if not self.key or not questions:
            return {}
        body = {"state": state, "model": self.model, "questions": questions}
        try:
            res = await asyncio.to_thread(_post, JEV_URL, body, self.key, self.timeout)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            log.warning("Jev失敗: %s", e)
            return {}
        return parse_answers(res.get("answers", {}), questions)


def parse_answers(answers, questions):
    out = {}
    for k in questions:
        a = answers.get(k)
        if not a:
            continue
        if a.get("type") == "noul":
            out[k] = float(a["noul"])
        elif a.get("type") == "choice":
            out[k] = (a["choice"], {c: float(p) for c, p in a.get("probabilities", {}).items()})
    return out


class LLM:
    def __init__(self, key, models, timeout=40):
        self.key, self.models, self.timeout = key, list(models), timeout

    async def chat(self, messages, temperature=1.0):
        """JSON で返させる。前のモデルが失敗したら次のモデル。全滅なら None。"""
        payload = {"messages": messages, "temperature": temperature, "reasoning": {"enabled": False},
                   "response_format": {"type": "json_object"}}
        for model in self.models:
            try:
                res = await asyncio.to_thread(_post, OR_URL, {**payload, "model": model}, self.key, self.timeout)
                text = res["choices"][0]["message"]["content"]
                if text and text.strip():
                    return text.strip().removeprefix("```json").removesuffix("```").strip()
            except (urllib.error.URLError, TimeoutError, ValueError, OSError, KeyError, IndexError) as e:
                log.warning("%s 失敗: %s", model, e)
        return None


def loads(text):
    """LLM の JSON。壊れていれば {}。"""
    try:
        v = json.loads(text or "")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}
