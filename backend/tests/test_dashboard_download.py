import csv
import importlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess

import pytest
import requests


DASHBOARD = "7769c554-2a58-4914-b727-6b2d4b178ffc"
METRIC = "8f8f404e-93ea-46e4-bdf9-6de5078bd33a"
DATASET = "4008b3e5-a75c-4efe-ac1f-4dd7c7e26a65"
SIGNED = "https://numetric-cache-prod.s3-fips.us-west-2.amazonaws.com/njdot/downloadReports/" + "a"*64 + "?X-Amz-Signature=secret-signature"
HEADERS = ["id_cr","Year","dateofcrash","casenumber","County","Municipality","Highest Injury Severity Rating","Geopoint (Calculated)"]


def mod():
    return importlib.import_module("scripts.download_njdot_dashboard")


def csv_bytes(rows=None):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(rows or [["1","2025","2025-01-01","case-1","Mercer","Princeton, Mercer","Possible Injury (C)",' {"lon":-74.5,"lat":40.3}']])
    return output.getvalue().encode()


class Response:
    def __init__(self, value=None, *, content=b"", status=200, headers=None):
        self.value, self.content, self.status_code = value, content, status
        self.headers = headers or {}
        self.text = content.decode("utf8")
    def json(self):
        return self.value
    def iter_content(self, chunk_size):
        yield self.content
    def close(self):
        pass


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def request(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        result = self.responses.pop(0)
        if isinstance(result,Exception):
            raise result
        return result


def metadata(export_rows=1):
    return [Response(content=f'portal={{"entityToken":"public-token","dashboardIds":["{DASHBOARD}"]}}'.encode()),
            Response({"success":True,"data":{"id":DASHBOARD,"version":1,"metrics":{METRIC:{
                "dataSchema":{"datasetId":DATASET,"shownColumns":["id_cr","year"]},
                "vizSchema":{"columnOrder":["id_cr","year","Light Condition"]}}},
                "content":{"views":[{"visuals":{"xxl":[{"id":METRIC,"displayName":"Crash Data"}]}}]}}}),
            Response({"success":True,"data":{"rows":[],"totalRows":1}}),
            Response({"success":True,"data":{"rows":[],"totalRows":export_rows}})]


def test_download_module_exists():
    assert importlib.util.find_spec("scripts.download_njdot_dashboard") is not None


def test_public_download_polls_and_atomically_validates_count_and_manifest(tmp_path):
    payload = csv_bytes()
    session = Session(metadata()+[Response({"status":"FILE_IN_PROGRESS"}),
                     Response({"status":"FILE_READY","link":SIGNED}),
                     Response(content=payload,headers={"Content-Length":str(len(payload))})])
    downloader = mod().DashboardDownloader(cache_dir=tmp_path,session=session,sleep=lambda _:None)
    report = downloader.acquire(start_year=2019,end_year=2025)
    assert report["status"] == "succeeded"
    assert report["csv"]["rows"] == report["expected_rows"] == 1
    assert report["csv"]["years"] == {"2025":1}
    assert len(report["csv"]["sha256"]) == 64
    assert (tmp_path / "Crash-2019-2025.csv").read_bytes() == payload
    assert not list(tmp_path.glob("*.part"))
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    search, download = session.calls[3],session.calls[4]
    assert search[2]["json"]["filters"] == download[2]["json"]["filters"]
    assert download[2]["json"]["filters"] == []
    assert session.calls[2][2]["json"]["filters"][0]["script"]["params"]["param2"] == 2025
    saved = (tmp_path / "manifest.json").read_text()
    assert "public-token" not in saved and "secret-signature" not in saved


@pytest.mark.parametrize("url",[
    "http://numetric-cache-prod.s3-fips.us-west-2.amazonaws.com/njdot/downloadReports/"+"a"*64,
    "https://evil.example/njdot/downloadReports/"+"a"*64,
    "https://numetric-cache-prod.s3-fips.us-west-2.amazonaws.com.evil.example/njdot/downloadReports/"+"a"*64,
    "https://user@numetric-cache-prod.s3-fips.us-west-2.amazonaws.com/njdot/downloadReports/"+"a"*64,
    "https://numetric-cache-prod.s3-fips.us-west-2.amazonaws.com/other/key",
])
def test_rejects_unexpected_signed_download_destinations(tmp_path,url):
    session = Session(metadata()+[Response({"status":"FILE_READY","link":url})])
    with pytest.raises(mod().AcquisitionError,match="destination"):
        mod().DashboardDownloader(cache_dir=tmp_path,session=session).acquire(start_year=2019,end_year=2025)
    assert len(session.calls) == 5


@pytest.mark.parametrize("rows,expected,reason",[
    ([["1","2026","2026-01-01","x","Mercer","Princeton","Fatal Injury (K)",""]],1,"year"),
    ([["1","2025","2024-01-01","x","Mercer","Princeton","Fatal Injury (K)",""]],1,"date"),
    ([["1","2025","2025-01-01","x","Mercer","Princeton","Fatal Injury (K)",""]]*2,2,"duplicate"),
    ([["1","2025","2025-01-01","x","Mercer","Princeton","Fatal Injury (K)",""]],2,"count"),
])
def test_csv_validation_rejects_partial_or_wrong_exports(tmp_path,rows,expected,reason):
    path=tmp_path/"input.csv";path.write_bytes(csv_bytes(rows))
    with pytest.raises(mod().AcquisitionError,match=reason):
        mod().validate_csv(path,start_year=2019,end_year=2025,expected_rows=expected)


def test_failed_download_preserves_existing_cache_and_redacts_errors(tmp_path):
    target=tmp_path/"Crash-2019-2025.csv";target.write_bytes(b"prior validated cache")
    manifest=tmp_path/"manifest.json"
    prior={"status":"succeeded","snapshot":"prior validated manifest"}
    manifest.write_text(json.dumps(prior))
    session=Session(metadata()+[Response({"status":"FILE_READY","link":SIGNED}),
                               requests.ConnectionError("private?entityToken=public-token&X-Amz-Signature=secret-signature")])
    with pytest.raises(mod().AcquisitionError) as error:
        mod().DashboardDownloader(cache_dir=tmp_path,session=session,retries=1).acquire(start_year=2019,end_year=2025,refresh=True)
    assert "public-token" not in str(error.value) and "secret-signature" not in str(error.value)
    assert target.read_bytes()==b"prior validated cache"
    assert json.loads(manifest.read_text()) == prior


def test_invalid_cache_recovers_without_manual_refresh(tmp_path):
    (tmp_path/"Crash-2019-2025.csv").write_bytes(b"interrupted previous attempt")
    (tmp_path/"manifest.json").write_text('{"status":"failed"}')
    payload=csv_bytes()
    session=Session(metadata()+[Response({"status":"FILE_READY","link":SIGNED}),Response(content=payload)])
    report=mod().DashboardDownloader(cache_dir=tmp_path,session=session).acquire(start_year=2019,end_year=2025)
    assert report["status"] == "succeeded"
    assert (tmp_path/"Crash-2019-2025.csv").read_bytes() == payload


def test_local_full_export_is_filtered_and_reconciled_to_public_count(tmp_path):
    source=tmp_path/"full.csv"
    source.write_bytes(csv_bytes([
        ["1","2025","2025-01-01","x","Mercer","Princeton","Possible Injury (C)",""],
        ["2","2026","2026-01-01","y","Mercer","Princeton","Possible Injury (C)",""],
    ]))
    downloader=mod().DashboardDownloader(cache_dir=tmp_path/"cache",session=Session(metadata(export_rows=2)))
    report=downloader.acquire(start_year=2019,end_year=2025,input_csv=source)
    assert report["csv"]["rows"] == 1
    assert report["source"]["kind"] == "filtered_public_export"
    assert report["source"]["excluded_rows"] == 1
    assert report["source"]["sha256"]


def test_poll_timeout_is_bounded(tmp_path):
    session=Session(metadata()+[Response({"status":"FILE_IN_PROGRESS"})])
    clock=iter([0,2])
    downloader=mod().DashboardDownloader(cache_dir=tmp_path,session=session,max_wait=1,clock=lambda:next(clock))
    with pytest.raises(mod().AcquisitionError,match="timed out"):
        downloader.acquire(start_year=2019,end_year=2025)


def test_extra_column_request_cannot_succeed_when_server_omits_headers(tmp_path):
    responses=metadata()
    responses[1].value["data"]["metrics"][METRIC]["vizSchema"]["columnOrder"].extend(mod().EXTRA_COLUMNS)
    session=Session(responses+[Response({"status":"FILE_READY","link":SIGNED}),Response(content=csv_bytes())])
    with pytest.raises(mod().AcquisitionError,match="additional.*columns"):
        mod().DashboardDownloader(cache_dir=tmp_path,session=session).acquire(start_year=2019,end_year=2025,extra_columns=True)
    assert not (tmp_path/"Crash-2019-2025.csv").exists()


def test_request_matches_working_ui_order_and_omits_unavailable_time_field(tmp_path):
    responses=metadata()
    metric=responses[1].value["data"]["metrics"][METRIC]
    metric["dataSchema"]["shownColumns"]=["year","Time of Crash","id_cr"]
    metric["vizSchema"]["columnOrder"]=["id_cr","Time of Crash","year"]
    session=Session(responses+[Response({"status":"FILE_READY","link":SIGNED}),Response(content=csv_bytes())])
    mod().DashboardDownloader(cache_dir=tmp_path,session=session).acquire(start_year=2019,end_year=2025)
    assert session.calls[4][2]["json"]["columns"] == ["id_cr","year"]


@pytest.mark.parametrize("retrieved_at", [None, "", "  ", "2026-09-13T06:00:00+00:00"])
def test_runonce_accepts_manifest_with_unknown_retrieval_time(monkeypatch, retrieved_at):
    wrapper = Path(__file__).parents[2] / "deploy/run-once/09-import-dashboard-through-2025.sh"
    body = wrapper.read_text().split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    source = {} if retrieved_at is None else {"retrieved_at": retrieved_at}
    manifest = {"status":"succeeded", "start_year":2019, "end_year":2025,
                "csv_path":"/srv/data/raw/njdot-dashboard/Crash-2019-2025.csv",
                "csv":{"sha256":"verified", "rows":1}, "source":source}
    original_read = Path.read_text
    def read_manifest(path, *args, **kwargs):
        if path.name == "njdot-dashboard-download-report.json":
            return json.dumps(manifest)
        return original_read(path, *args, **kwargs)
    calls = []
    monkeypatch.setattr(Path, "read_text", read_manifest)
    monkeypatch.setattr(mod(), "file_hash", lambda _: "verified")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    exec(compile(body, str(wrapper), "exec"), {})
    argv, kwargs = calls[0]
    assert kwargs == {"check":True}
    if isinstance(retrieved_at, str) and retrieved_at.strip():
        assert argv[argv.index("--retrieved-at")+1] == retrieved_at
    else:
        assert "--retrieved-at" not in argv
    assert "--apply" in argv and "--allow-new-historical" in argv
