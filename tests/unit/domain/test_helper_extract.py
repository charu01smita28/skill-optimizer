"""Tests for ``propose_helper_extract`` (pairs with D012). Stub ``LLMClient``."""
from __future__ import annotations

from dataclasses import dataclass

from skill_optimizer.domain.mutations import propose_helper_extract
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.llm_client import LLMClientError

_SKILL_TEXT = (
    "---\n"
    "name: invoice-validator\n"
    "model: claude-haiku-4-5\n"
    "---\n"
    "\n"
    "# Invoice Validator\n"
    "\n"
    "## Process\n"
    "\n"
    "Implement `validate_invoice(invoice)` as a Python function, include it in "
    "full, then apply it to the input. Write the result to output.json.\n"
)

_CAPTURED_V1 = (
    "import json\n"
    "def validate_invoice(invoice):\n"
    "    subtotal = sum(li['quantity'] * li['unit_price'] for li in invoice['line_items'])\n"
    "    return {'computed': {'subtotal': subtotal}, 'valid': True}\n"
)

_CAPTURED_V2 = (
    "import json\n"
    "def round_2dp(x): return round(x, 2)\n"
    "def validate_invoice(inv):\n"
    "    sub = round_2dp(sum(li['quantity'] * li['unit_price'] for li in inv['line_items']))\n"
    "    return {'computed': {'subtotal': sub}, 'valid': True}\n"
)

_REWRITTEN_SKILL = (
    "---\n"
    "name: invoice-validator\n"
    "model: claude-haiku-4-5\n"
    "---\n"
    "\n"
    "# Invoice Validator\n"
    "\n"
    "## Process\n"
    "\n"
    "1. Run `python helper.py sample_inputs/<input-file>` — it reads the invoice "
    "JSON, recomputes subtotal/discount/tax/total, and writes the result to "
    "output.json.\n"
    "2. Read output.json.\n"
)

_HELPER_PY = (
    "import json, sys\n"
    "from decimal import Decimal, ROUND_HALF_UP\n"
    "\n"
    "def _r(x):\n"
    "    return float(Decimal(str(x)).quantize(Decimal('0.01'),"
    " rounding=ROUND_HALF_UP))\n"
    "\n"
    "def validate_invoice(invoice):\n"
    "    items = invoice['line_items']\n"
    "    subtotal = _r(sum(li['quantity'] * li['unit_price'] for li in items))\n"
    "    discount = _r(subtotal * invoice.get('discount_pct', 0) / 100)\n"
    "    tax = _r((subtotal - discount) * invoice.get('tax_rate', 0) / 100)\n"
    "    total = _r(subtotal - discount + tax + invoice.get('shipping', 0))\n"
    "    computed = {'subtotal': subtotal, 'discount': discount,\n"
    "                'tax': tax, 'total': total}\n"
    "    return {'computed': computed, 'valid': True}\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    inv = json.load(open(sys.argv[1]))\n"
    "    json.dump(validate_invoice(inv), open('output.json', 'w'), indent=2)\n"
)

_VALID_RESPONSE = (
    f"<HELPER_PY>\n{_HELPER_PY}</HELPER_PY>\n"
    f"<SKILL_MD>\n{_REWRITTEN_SKILL}</SKILL_MD>\n"
)


@dataclass
class _StubLLM:
    response: str = ""
    raise_error: bool = False
    last_system: str = ""
    last_user: str = ""
    last_model: str = ""

    def complete(self, system: str, user: str, model: str = "") -> str:
        self.last_system, self.last_user, self.last_model = system, user, model
        if self.raise_error:
            raise LLMClientError("stub error")
        return self.response


def _ev(ref: str, origin: str, code: str, defs: list[str]) -> dict:
    return {
        "trace_ref": ref, "origin": origin, "def_names": defs,
        "code": code, "fragment": code[:200],
    }


def _d012_finding(*, primary: str = "validate_invoice") -> Finding:
    return Finding(
        finding_id="skopt-2026-05-13-d012-invoice_validator-001",
        detector_id="D012",
        skill_id="invoice_validator",
        category="script_rederivation",
        observed_pattern=(
            f"The model re-derives `{primary}(...)` from scratch in 45 of 57 "
            f"runs — authored via Write and/or inline `python` each time."
        ),
        evidence=(
            _ev("run_001.jsonl", "write", _CAPTURED_V1, [primary]),
            _ev("run_002.jsonl", "write", _CAPTURED_V2, [primary, "round_2dp"]),
            _ev("run_003.jsonl", "bash", _CAPTURED_V1, [primary]),
        ),
        estimated_cost_pct=-20.1,
        estimated_latency_pct=-14.1,
        quality_risk="low",
        occurrences=45,
    )


def _propose(response: str, *, skill: str = _SKILL_TEXT, finding: Finding | None = None):
    return propose_helper_extract(
        finding or _d012_finding(),
        current_skill_text=skill,
        llm_client=_StubLLM(response=response),
    )


def test_proposal_carries_helper_py_in_new_files() -> None:
    proposal = _propose(_VALID_RESPONSE)

    assert proposal is not None
    assert proposal.tier == "2"
    assert proposal.mutation_type == "helper_extract"
    assert proposal.patch.target_relative_path == "SKILL.md"
    assert proposal.patch.full_file is True
    assert proposal.patch.before_text == _SKILL_TEXT
    assert "helper.py" in proposal.patch.new_files
    assert "def validate_invoice" in proposal.patch.new_files["helper.py"]
    assert '__name__ == "__main__"' in proposal.patch.new_files["helper.py"]


def test_rewriter_user_prompt_includes_primary_and_captured_code() -> None:
    llm = _StubLLM(response=_VALID_RESPONSE)
    propose_helper_extract(_d012_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert "<RECURRING_FUNCTION_NAME>validate_invoice</RECURRING_FUNCTION_NAME>" in llm.last_user
    assert "run_001.jsonl" in llm.last_user
    assert "def validate_invoice" in llm.last_user
    assert "<CURRENT_SKILL_MD>" in llm.last_user


def test_skill_md_after_text_starts_with_frontmatter() -> None:
    proposal = _propose("Sure, here you go:\n\n" + _VALID_RESPONSE)

    assert proposal is not None
    assert proposal.patch.after_text.startswith("---")
    assert "Sure, here" not in proposal.patch.after_text


def test_returns_none_when_llm_raises() -> None:
    llm = _StubLLM(raise_error=True)
    finding = _d012_finding()
    assert propose_helper_extract(
        finding, current_skill_text=_SKILL_TEXT, llm_client=llm,
    ) is None


def test_returns_none_when_response_missing_helper_tag() -> None:
    assert _propose(f"<SKILL_MD>\n{_REWRITTEN_SKILL}</SKILL_MD>\n") is None


def test_returns_none_when_helper_lacks_primary_def() -> None:
    bad_helper = (
        'import json, sys\n'
        'def something_else(x):\n    return x\n'
        'if __name__ == "__main__":\n    pass\n'
    )
    bad = f"<HELPER_PY>\n{bad_helper}</HELPER_PY>\n<SKILL_MD>\n{_REWRITTEN_SKILL}</SKILL_MD>\n"
    assert _propose(bad) is None


def test_returns_none_when_helper_lacks_main_guard() -> None:
    bad_helper = (
        "import json, sys\n"
        "def validate_invoice(inv):\n"
        "    return {'valid': True}\n"
        "validate_invoice({})\n"
    )
    bad = f"<HELPER_PY>\n{bad_helper}</HELPER_PY>\n<SKILL_MD>\n{_REWRITTEN_SKILL}</SKILL_MD>\n"
    assert _propose(bad) is None


def test_returns_none_when_skill_md_implausible() -> None:
    bad = (
        f"<HELPER_PY>\n{_HELPER_PY}</HELPER_PY>\n"
        f"<SKILL_MD>\njust prose, no frontmatter</SKILL_MD>\n"
    )
    assert _propose(bad) is None


def test_returns_none_when_finding_has_no_primary_function_name() -> None:
    f = Finding(
        finding_id="f-d012",
        detector_id="D012",
        skill_id="x",
        category="script_rederivation",
        observed_pattern="model re-derives stuff",  # no `name(` pattern
        evidence=(),
        estimated_cost_pct=-10.0,
        estimated_latency_pct=-7.0,
        quality_risk="low",
        occurrences=3,
    )
    llm = _StubLLM(response=_VALID_RESPONSE)
    assert propose_helper_extract(
        f, current_skill_text=_SKILL_TEXT, llm_client=llm,
    ) is None
    assert llm.last_user == ""  # short-circuits before LLM call


def test_returns_none_on_noop_skill_md_rewrite() -> None:
    same = f"<HELPER_PY>\n{_HELPER_PY}</HELPER_PY>\n<SKILL_MD>\n{_SKILL_TEXT}</SKILL_MD>\n"
    assert _propose(same) is None
