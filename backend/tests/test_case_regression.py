"""Selected, explicitly synthetic language variants from verified case themes."""
import json
from pathlib import Path

import pytest

from ff_agent.safety import assess_request

CORPUS=json.loads((Path(__file__).resolve().parents[2]/'docs/cases/sprint-scenarios.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case',CORPUS['variants'],ids=lambda v:v['id']+'-'+v['label'])
def test_case_derived_risk_hold_preserves_normal_negation_and_consultation(case):
    finding=assess_request(case['text'])
    assert bool(finding and finding['hold']) is case['expected_hold'],case['text']
