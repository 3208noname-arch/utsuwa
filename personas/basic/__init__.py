"""体も気分も持たない、いちばん小さな人格の部品。今の様子は予定（SCHEDULE.md）だけで決める。

送る前に弾く正規表現だけを、config.yaml の `guards` から読む。
"""
import re

from harness.persona import Persona


class Basic(Persona):
    def guards(self):
        return [re.compile(p) for p in self.agent.config.get("guards") or []]


PERSONA = Basic
