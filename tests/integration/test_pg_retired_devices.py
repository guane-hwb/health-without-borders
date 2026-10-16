"""
/patients/retired-devices against PostgreSQL: `retired_at` is a timestamptz
the database sets (now(), the start of the sync's transaction), and `since`
is compared with it there, not in Python.
"""
from copy import deepcopy
from datetime import datetime, timedelta

from app.services.retired_device_service import retired_device_hash
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD

URL = "/api/v1/patients/retired-devices"
UID = MOCK_PATIENT_PAYLOAD["device_uid"]


def _list(api, tokens, since=None):
    params = {} if since is None else {"since": since}
    response = api.get(URL, headers=tokens["headers"], params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_since_finds_a_device_retired_after_the_previous_list(api, staff):
    doc = staff["tokens"]["doc_a"]
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    assert api.post("/api/v1/patients/sync", headers=doc["headers"], json=payload).status_code == 201
    first = _list(api, doc)
    assert first["uid_hashes"] == []

    payload.update(device_uid="04:A2:NEW:UID", retiredDeviceReason="lost")
    assert api.post("/api/v1/patients/sync", headers=doc["headers"], json=payload).status_code == 201

    later = _list(api, staff["tokens"]["nurse_a"], since=first["server_time"])
    assert later["uid_hashes"] == [retired_device_hash(UID)]

    an_hour_later = datetime.fromisoformat(later["server_time"]) + timedelta(hours=1)
    assert _list(api, doc, since=an_hour_later.isoformat())["uid_hashes"] == []


def test_superadmin_is_refused(api, staff):
    response = api.get(URL, headers=staff["tokens"]["sa"]["headers"])
    assert response.status_code == 403
