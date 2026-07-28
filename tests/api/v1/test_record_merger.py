"""
Unit tests for app/services/record_merger.py
"""

import logging
import uuid

import pytest

from app.services.record_merger import (
    _assign_missing_keys,
    _merge_items_by_key,
    _preserve_consent_signature,
    merge_patient_records,
)

MERGER_LOGGER_NAME = "app.services.record_merger"


@pytest.fixture
def merger_logs(caplog):
    """Attach caplog handler directly to the merger logger.

    The app's logging config may disable propagation, which prevents
    caplog (rooted at the root logger) from seeing records. This fixture
    bypasses that by adding the caplog handler to the target logger.
    """
    target = logging.getLogger(MERGER_LOGGER_NAME)
    target.addHandler(caplog.handler)
    caplog.handler.setLevel(logging.DEBUG)
    yield caplog
    target.removeHandler(caplog.handler)

# ============================================================================
# HELPERS — minimal record factories
# ============================================================================

def _make_visit(encounter_id: str = None, note: str = "visit") -> dict:
    return {
        "encounterIdentifier": encounter_id or str(uuid.uuid4()),
        "startDateTime": "2026-04-01T10:00:00",
        "endDateTime": None,
        "careModality": "01",
        "serviceGroup": "01",
        "careEnvironment": "05",
        "clinicalEvaluation": {"historyOfCurrentIllness": note},
        "diagnosis": [],
        "diagnosisType": "01",
        "riskFactors": [],
    }


def _make_vaccine(vaccination_id: str = None, name: str = "BCG") -> dict:
    return {
        "vaccinationId": vaccination_id or str(uuid.uuid4()),
        "date": "2026-03-15",
        "vaccineName": name,
        "vaccineCode": "19",
        "dose": 1,
        "administratedBy": "Nurse A",
        "administratedAt": "Hospital Meoz",
        "status": "completed",
    }


def _make_record(
    visits: list = None,
    vaccines: list = None,
    allergies: list = None,
) -> dict:
    return {
        "patientId": "PAT-001",
        "device_uid": "NFC-001",
        "patientInfo": {"firstName": "Santiago", "firstLastName": "Rodríguez"},
        "guardianInfo": {"name": "María Pérez", "phone": "+57300000"},
        "backgroundHistory": {"chronicConditions": [], "familyHistory": []},
        "allergies": allergies or [],
        "medicalHistory": visits or [],
        "vaccinationRecord": vaccines or [],
    }


# ============================================================================
# _assign_missing_keys
# ============================================================================

class TestAssignMissingKeys:
    def test_assigns_uuid_to_items_without_key(self):
        items = [{"data": "value"}, {"encounterIdentifier": "existing-id"}]
        _assign_missing_keys(items, "encounterIdentifier")

        assert items[0]["encounterIdentifier"] is not None
        assert len(items[0]["encounterIdentifier"]) == 36  # UUID format
        assert items[1]["encounterIdentifier"] == "existing-id"

    def test_does_not_overwrite_existing_keys(self):
        items = [{"vaccinationId": "keep-me"}]
        _assign_missing_keys(items, "vaccinationId")
        assert items[0]["vaccinationId"] == "keep-me"

    def test_handles_empty_list(self):
        items = []
        _assign_missing_keys(items, "key")
        assert items == []

    def test_assigns_unique_uuids(self):
        items = [{"x": 1}, {"x": 2}, {"x": 3}]
        _assign_missing_keys(items, "id")
        ids = [item["id"] for item in items]
        assert len(set(ids)) == 3  # all unique


# ============================================================================
# _merge_items_by_key
# ============================================================================

class TestMergeItemsByKey:
    def test_disjoint_items_both_preserved(self):
        """Server and incoming have completely different items → all kept."""
        server = [_make_visit("A"), _make_visit("B")]
        incoming = [_make_visit("C"), _make_visit("D")]

        merged = _merge_items_by_key(server, incoming, "encounterIdentifier", "visit")
        keys = [v["encounterIdentifier"] for v in merged]

        assert len(merged) == 4
        assert set(keys) == {"A", "B", "C", "D"}

    def test_overlapping_items_server_wins(self):
        """Items with same key → server version is kept."""
        server_visit = _make_visit("A", note="server version")
        incoming_visit = _make_visit("A", note="incoming version")

        merged = _merge_items_by_key(
            [server_visit], [incoming_visit], "encounterIdentifier", "visit"
        )

        assert len(merged) == 1
        assert merged[0]["clinicalEvaluation"]["historyOfCurrentIllness"] == "server version"

    def test_server_items_preserved_when_incoming_missing(self):
        """Incoming is missing a server visit → server visit preserved."""
        server = [_make_visit("A"), _make_visit("B"), _make_visit("C")]
        incoming = [_make_visit("A")]  # missing B and C

        merged = _merge_items_by_key(server, incoming, "encounterIdentifier", "visit")
        keys = [v["encounterIdentifier"] for v in merged]

        assert len(merged) == 3
        assert set(keys) == {"A", "B", "C"}

    def test_new_incoming_items_appended_at_end(self):
        """New items from incoming are appended after server items."""
        server = [_make_visit("A")]
        incoming = [_make_visit("A"), _make_visit("B")]

        merged = _merge_items_by_key(server, incoming, "encounterIdentifier", "visit")

        assert len(merged) == 2
        assert merged[0]["encounterIdentifier"] == "A"
        assert merged[1]["encounterIdentifier"] == "B"

    def test_empty_server_takes_all_incoming(self):
        """First sync — no server data, all incoming items added."""
        incoming = [_make_visit("A"), _make_visit("B")]

        merged = _merge_items_by_key([], incoming, "encounterIdentifier", "visit")

        assert len(merged) == 2

    def test_empty_incoming_preserves_all_server(self):
        """Device with no new items — all server items preserved."""
        server = [_make_visit("A"), _make_visit("B")]

        merged = _merge_items_by_key(server, [], "encounterIdentifier", "visit")

        assert len(merged) == 2

    def test_both_empty(self):
        merged = _merge_items_by_key([], [], "encounterIdentifier", "visit")
        assert merged == []

    def test_server_order_preserved(self):
        """Server items keep their original order."""
        server = [_make_visit("C"), _make_visit("A"), _make_visit("B")]
        incoming = [_make_visit("D")]

        merged = _merge_items_by_key(server, incoming, "encounterIdentifier", "visit")

        assert merged[0]["encounterIdentifier"] == "C"
        assert merged[1]["encounterIdentifier"] == "A"
        assert merged[2]["encounterIdentifier"] == "B"
        assert merged[3]["encounterIdentifier"] == "D"


# ============================================================================
# merge_patient_records — full record merge
# ============================================================================

class TestMergePatientRecords:
    def test_visits_merged_by_encounter_id(self):
        """Two devices with different visits → all visits in merged record."""
        visit_a = _make_visit("ENC-A")
        visit_b = _make_visit("ENC-B")
        visit_c = _make_visit("ENC-C")

        server = _make_record(visits=[visit_a, visit_b])
        incoming = _make_record(visits=[visit_a, visit_c])

        merged = merge_patient_records(server, incoming)
        enc_ids = [v["encounterIdentifier"] for v in merged["medicalHistory"]]

        assert len(merged["medicalHistory"]) == 3
        assert set(enc_ids) == {"ENC-A", "ENC-B", "ENC-C"}

    def test_vaccines_merged_by_vaccination_id(self):
        """Two devices with different vaccines → all vaccines in merged record."""
        vax_a = _make_vaccine("VAX-A", name="BCG")
        vax_b = _make_vaccine("VAX-B", name="Hepatitis B")
        vax_c = _make_vaccine("VAX-C", name="Polio")

        server = _make_record(vaccines=[vax_a, vax_b])
        incoming = _make_record(vaccines=[vax_a, vax_c])

        merged = merge_patient_records(server, incoming)
        vax_ids = [v["vaccinationId"] for v in merged["vaccinationRecord"]]

        assert len(merged["vaccinationRecord"]) == 3
        assert set(vax_ids) == {"VAX-A", "VAX-B", "VAX-C"}

    def test_allergies_taken_from_incoming(self):
        """Allergies are declarative — incoming version wins."""
        server = _make_record(allergies=[{"allergen": "Penicilina"}])
        incoming = _make_record(
            allergies=[{"allergen": "Penicilina"}, {"allergen": "Aspirina"}]
        )

        merged = merge_patient_records(server, incoming)

        assert len(merged["allergies"]) == 2
        assert merged["allergies"][1]["allergen"] == "Aspirina"

    def test_background_history_taken_from_incoming(self):
        """Background history is declarative — incoming version wins."""
        server = _make_record()
        server["backgroundHistory"] = {"chronicConditions": [{"chronicDescription": "old"}]}

        incoming = _make_record()
        incoming["backgroundHistory"] = {"chronicConditions": [
            {"chronicDescription": "old"},
            {"chronicDescription": "new"},
        ]}

        merged = merge_patient_records(server, incoming)

        assert len(merged["backgroundHistory"]["chronicConditions"]) == 2

    def test_guardian_info_taken_from_incoming(self):
        """Guardian info is updatable — incoming version wins."""
        server = _make_record()
        incoming = _make_record()
        incoming["guardianInfo"]["phone"] = "+57311111"

        merged = merge_patient_records(server, incoming)

        assert merged["guardianInfo"]["phone"] == "+57311111"

    def test_no_duplicate_visits_on_identical_sync(self):
        """Re-syncing the same data produces no duplicates."""
        visits = [_make_visit("ENC-A"), _make_visit("ENC-B")]
        server = _make_record(visits=visits)
        incoming = _make_record(visits=visits)

        merged = merge_patient_records(server, incoming)

        assert len(merged["medicalHistory"]) == 2

    def test_server_visits_preserved_order_for_delta(self):
        """
        Server visits come first in the merged list so the index-based
        delta logic (synced_visit_count) continues to work correctly.
        """
        visit_a = _make_visit("ENC-A")
        visit_b = _make_visit("ENC-B")
        visit_c = _make_visit("ENC-C")  # new from incoming

        server = _make_record(visits=[visit_a, visit_b])
        incoming = _make_record(visits=[visit_a, visit_c])

        merged = merge_patient_records(server, incoming)
        enc_ids = [v["encounterIdentifier"] for v in merged["medicalHistory"]]

        # Server visits first, new incoming appended at end
        assert enc_ids == ["ENC-A", "ENC-B", "ENC-C"]


# ============================================================================
# Logging verification
# ============================================================================

class TestMergeLogging:
    def test_logs_warning_when_preserving_server_items(self, merger_logs):
        """When incoming is missing server visits, a warning is logged."""
        server = _make_record(visits=[_make_visit("A"), _make_visit("B")])
        incoming = _make_record(visits=[_make_visit("A")])  # missing B

        merge_patient_records(server, incoming)

        assert any("preserved" in r.message.lower() for r in merger_logs.records)

    def test_logs_info_when_adding_new_items(self, merger_logs):
        """When incoming has new items, an info message is logged."""
        server = _make_record(visits=[_make_visit("A")])
        incoming = _make_record(visits=[_make_visit("A"), _make_visit("B")])

        merge_patient_records(server, incoming)

        assert any("added" in r.message.lower() for r in merger_logs.records)

    def test_no_warning_on_clean_sync(self, merger_logs):
        """When incoming has all server items, no warning is logged."""
        visits = [_make_visit("A"), _make_visit("B")]
        server = _make_record(visits=visits)
        incoming = _make_record(visits=visits)

        merge_patient_records(server, incoming)

        warnings = [r for r in merger_logs.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 0


# ============================================================================
# Legacy data (items without UUIDs)
# ============================================================================

class TestLegacyDataMigration:
    def test_server_items_without_key_get_uuid(self):
        """Legacy visits without encounterIdentifier get a UUID assigned."""
        legacy_visit = {
            "startDateTime": "2026-01-01T10:00:00",
            "clinicalEvaluation": {"historyOfCurrentIllness": "legacy"},
            "diagnosis": [],
        }
        server = _make_record(visits=[legacy_visit])
        incoming = _make_record(visits=[_make_visit("NEW")])

        merged = merge_patient_records(server, incoming)

        assert len(merged["medicalHistory"]) == 2
        # Legacy visit now has an assigned UUID
        assert merged["medicalHistory"][0].get("encounterIdentifier") is not None
        assert merged["medicalHistory"][1]["encounterIdentifier"] == "NEW"

    def test_legacy_vaccines_without_key_get_uuid(self):
        """Legacy vaccines without vaccinationId get a UUID assigned."""
        legacy_vax = {
            "date": "2026-01-01",
            "vaccineName": "BCG",
            "vaccineCode": "19",
            "dose": 1,
            "administratedBy": "Nurse",
            "administratedAt": "Clinic",
            "status": "completed",
        }
        server = _make_record(vaccines=[legacy_vax])
        incoming = _make_record(vaccines=[_make_vaccine("NEW-VAX")])

        merged = merge_patient_records(server, incoming)

        assert len(merged["vaccinationRecord"]) == 2
        assert merged["vaccinationRecord"][0].get("vaccinationId") is not None

# ============================================================================
# _preserve_consent_signature — guardian signature protection on merge
# ============================================================================

def _guardian(signature=None, *, with_consent=True, **consent_extra) -> dict:
    """A guardian dict, optionally with a consent block and signature.

    ``with_consent=False`` omits the consent block entirely (as a guardian
    reconstructed from a card that never captured consent would look).
    """
    g = {"name": "María Pérez", "phone": "+57300000"}
    if with_consent:
        consent = {"accepted": True, "acceptedAt": "2026-04-01T10:00:00"}
        consent.update(consent_extra)
        if signature is not None:
            consent["signatureBase64"] = signature
        g["consent"] = consent
    return g


class TestPreserveConsentSignature:
    def test_restores_signature_when_incoming_omits_it(self):
        """Card-sourced guardian (signature stripped) keeps the server PNG."""
        server = _guardian(signature="iVBORw0KGgo=")
        incoming = _guardian()  # consent present but no signatureBase64
        result = _preserve_consent_signature(server, incoming)
        assert result["consent"]["signatureBase64"] == "iVBORw0KGgo="
        # Other incoming consent fields are retained
        assert result["consent"]["accepted"] is True

    def test_incoming_signature_is_not_overwritten(self):
        """An online edit carrying its own signature wins over the server's."""
        server = _guardian(signature="OLD-SERVER-PNG")
        incoming = _guardian(signature="NEW-DEVICE-PNG")
        result = _preserve_consent_signature(server, incoming)
        assert result["consent"]["signatureBase64"] == "NEW-DEVICE-PNG"

    def test_carries_whole_consent_when_incoming_has_none(self):
        """Incoming guardian without any consent block adopts the server's."""
        server = _guardian(signature="iVBORw0KGgo=", email="g@example.com")
        incoming = _guardian(with_consent=False)
        result = _preserve_consent_signature(server, incoming)
        assert result["consent"]["signatureBase64"] == "iVBORw0KGgo="
        assert result["consent"]["email"] == "g@example.com"

    def test_noop_when_server_has_no_signature(self):
        server = _guardian(with_consent=False)
        incoming = _guardian()
        result = _preserve_consent_signature(server, incoming)
        assert "signatureBase64" not in result["consent"]

    def test_does_not_mutate_incoming(self):
        server = _guardian(signature="iVBORw0KGgo=")
        incoming = _guardian()
        _preserve_consent_signature(server, incoming)
        assert "signatureBase64" not in incoming["consent"]

    def test_handles_missing_guardian_gracefully(self):
        # guardian2Info is commonly absent (None) — must pass through untouched.
        assert _preserve_consent_signature(None, None) is None
        server = _guardian(signature="iVBORw0KGgo=")
        assert _preserve_consent_signature(server, None) is None


class TestMergePreservesGuardianSignature:
    def test_merge_restores_stripped_guardian_signature(self):
        """End-to-end: a card-sourced record keeps the server signature."""
        server = _make_record()
        server["guardianInfo"] = _guardian(signature="SERVER-PNG")
        incoming = _make_record()
        incoming["guardianInfo"] = _guardian()  # stripped by the card writer

        merged = merge_patient_records(server, incoming)

        assert merged["guardianInfo"]["consent"]["signatureBase64"] == "SERVER-PNG"

    def test_merge_restores_second_guardian_signature(self):
        server = _make_record()
        server["guardian2Info"] = _guardian(signature="G2-SERVER-PNG")
        incoming = _make_record()
        incoming["guardian2Info"] = _guardian()

        merged = merge_patient_records(server, incoming)

        assert (
            merged["guardian2Info"]["consent"]["signatureBase64"]
            == "G2-SERVER-PNG"
        )
