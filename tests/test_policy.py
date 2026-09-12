"""Contract tests for the SIPO policy schema. No API calls."""

import pytest
from pydantic import ValidationError

from modules.policy import BaselinePolicy, to_baseline_row


def _null():
    return {"value": None, "page": None, "source_text": None}


def _fig(value, page=3, quote="quoted"):
    return {"value": value, "page": page, "source_text": quote}


def _payload(**overrides):
    groups = {
        "identity": ["scheme_name", "manager_name", "sipo_date"],
        "returns": ["minimum_cash_return_percent", "cash_return_basis",
                    "occupancy_floor_percent", "nta_floor_percent"],
        "debt": ["lvr_ceiling_percent", "icr_floor", "hedging_minimum_percent",
                 "debt_policy_notes"],
        "conduct": ["distribution_suspension_triggers", "fee_entitlements", "capex_policy"],
    }
    payload = {g: {f: _null() for f in fields} for g, fields in groups.items()}
    payload["extraction_notes"] = None
    where = {f: g for g, fields in groups.items() for f in fields}
    for key, value in overrides.items():
        if key == "extraction_notes":
            payload[key] = value
        else:
            payload[where[key]][key] = value
    return payload


class TestContract:
    def test_every_field_is_required_and_nullable(self):
        schema = BaselinePolicy.model_json_schema()
        objects = [schema] + [d for d in schema.get("$defs", {}).values()
                              if d.get("type") == "object"]
        for obj in objects:
            assert sorted(obj["properties"]) == sorted(obj.get("required", [])), obj.get("title")

    def test_top_level_stays_under_the_grammar_limit(self):
        assert len(BaselinePolicy.model_json_schema()["properties"]) <= 10

    def test_a_silent_sipo_is_expressible(self):
        """Not every SIPO states a hedging minimum; that must be recordable."""
        policy = BaselinePolicy.model_validate(_payload())
        assert policy.debt.hedging_minimum_percent.value is None

    def test_omitting_a_field_is_rejected(self):
        payload = _payload()
        del payload["debt"]["lvr_ceiling_percent"]
        with pytest.raises(ValidationError):
            BaselinePolicy.model_validate(payload)


class TestUnits:
    def test_ratios_stay_multiples_and_percents_stay_whole(self):
        policy = BaselinePolicy.model_validate(_payload(
            icr_floor=_fig(2.0), lvr_ceiling_percent=_fig(55.0),
            minimum_cash_return_percent=_fig(7.0)))
        assert policy.debt.icr_floor.value == 2.0
        assert policy.debt.lvr_ceiling_percent.value == 55.0
        assert policy.returns.minimum_cash_return_percent.value == 7.0


class TestBaselineMapping:
    def _augusta(self):
        return BaselinePolicy.model_validate(_payload(
            minimum_cash_return_percent=_fig(7.0),
            cash_return_basis=_fig("the investor's original equity amount"),
            occupancy_floor_percent=_fig(90.0),
            nta_floor_percent=_fig(85.0),
            lvr_ceiling_percent=_fig(55.0),
            icr_floor=_fig(2.0),
            hedging_minimum_percent=_fig(50.0),
        ))

    def test_maps_onto_baseline_columns(self):
        row = to_baseline_row(self._augusta())
        assert row["im_forecast_distribution_rate"] == 7.0
        assert row["lvr_covenant_threshold"] == 55.0
        assert row["icr_covenant_threshold"] == 2.0

    def test_records_the_return_basis(self):
        """A return on original equity is not the same measure as one on current value."""
        assert "original equity" in to_baseline_row(self._augusta())["trust_deed_notes"]

    def test_notes_carry_every_stated_threshold(self):
        notes = to_baseline_row(self._augusta())["trust_deed_notes"]
        for fragment in ("min cash return 7", "occupancy floor 90", "LVR ceiling 55",
                         "ICR floor 2", "NTA floor 85", "hedging minimum 50"):
            assert fragment in notes

    def test_absent_fields_are_not_written(self):
        """A silent SIPO must never blank a value entered by hand."""
        row = to_baseline_row(BaselinePolicy.model_validate(_payload()))
        assert "im_forecast_distribution_rate" not in row
        assert "lvr_covenant_threshold" not in row
        assert "trust_deed_notes" not in row

    def test_partial_sipo_writes_only_what_it_states(self):
        row = to_baseline_row(BaselinePolicy.model_validate(
            _payload(lvr_ceiling_percent=_fig(50.0))))
        assert row["lvr_covenant_threshold"] == 50.0
        assert "icr_covenant_threshold" not in row
