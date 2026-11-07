"""Read the public map published by CET. Only request fields displayed by the report."""
import base64
import gzip
import json
import re
import sys
import urllib.request
from urllib.parse import urlsplit

CET = "https://www.cetsp.com.br/consultas/fiscalizacao-eletronica-do-transito.aspx"
FIELDS = ["CÓDIGO LOCAL", "DESCRIÇÃO DO LOCAL", "LATITUDE", "LONGITUDE", "ENQUADRAMENTO", "ENQUADRAMENTOS", "VELOCIDADE", "DESATIVAÇÃO", "ATIVAÇÃO"]


def fetch(url, key=None, payload=None):
    headers = {"User-Agent": "RadarSP/0.1"}
    if key:
        headers["X-PowerBI-ResourceKey"] = key
    if payload:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, json.dumps(payload).encode() if payload else None, headers)
    with urllib.request.urlopen(request, timeout=80) as response:
        data = response.read()
        return gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data


def rows():
    report = re.search(rb'https://app.powerbi.com/view\?r=([^"&]+)', fetch(CET))
    token = report.group(1).decode()
    key = json.loads(base64.urlsafe_b64decode(token + "==="))["k"]
    page = fetch("https://app.powerbi.com/view?r=" + token).decode()
    cluster = re.search(r"resolvedClusterUri = '([^']+)'", page).group(1)
    host = urlsplit(cluster).netloc.split(".")
    host[0] = host[0].replace("-redirect", "").replace("global-", "") + "-api"
    base = "https://" + ".".join(host)
    model = json.loads(fetch(f"{base}/public/reports/{key}/modelsAndExploration", key))["models"][0]["id"]
    table = "tblFiscalizacaoEletronica"
    columns = [{"Column":{"Expression":{"SourceRef":{"Source":"r"}},"Property":f},"Name":table+"."+f} for f in FIELDS]
    query = {"Version":2,"From":[{"Name":"r","Entity":table,"Type":0}],"Select":columns}
    binding = {"Primary":{"Groupings":[{"Projections":list(range(len(FIELDS))),"Subtotal":1}]},
               "DataReduction":{"DataVolume":3,"Primary":{"Window":{"Count":10000}}},"Version":1}
    command = {"SemanticQueryDataShapeCommand":{"Query":query,"Binding":binding,"ExecutionMetricsKind":1}}
    body = {"version":"1.0.0","queries":[{"Query":{"Commands":[command]},"ApplicationContext":{}}],"cancelQueries":[],"modelId":model}
    reply = json.loads(fetch(base+"/public/reports/querydata?synchronous=true", key, body))
    ds = reply["results"][0]["result"]["data"]["dsr"]["DS"][0]
    matrix = ds["PH"][0]["DM0"]
    if len(matrix) >= 10000 or ds.get("RT"):
        raise ValueError("Resultado incompleto. Verificar paginação.")
    previous = []
    schema = []
    result = []
    for item in matrix:
        schema = item.get("S",schema)
        if not schema:
            continue
        cells = iter(item.get("C",[]))
        row = []
        for col, spec in enumerate(schema):
            if item.get("R",0) & (1<<col):
                value = previous[col]
            elif item.get("Ø",0) & (1<<col):
                value = None
            else:
                value = next(cells)
                if "DN" in spec and isinstance(value,int):
                    value = ds["ValueDicts"][spec["DN"]][value]
            row.append(value)
        previous = row
        result.append(dict(zip(FIELDS,row)))
    return result


if __name__ == "__main__":
    result = rows()
    with open(sys.argv[1], "w", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False)
    print("Pontos consultados:",len(result))
