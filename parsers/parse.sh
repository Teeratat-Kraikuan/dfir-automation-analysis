#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  parse mft <input_MFT> <out_dir> [csv_name]"
  echo "  parse amcache <input_Amcache.hve> <out_dir> [csv_name]"
  echo "  parse evtx-dir <input_winevt_logs_dir> <out_dir> [csv_name]"
  exit 1
}

MFTE="/opt/tools/MFTECmd/MFTECmd.dll"
AMC="/opt/tools/AmcacheParser/AmcacheParser.dll"
EVTX="/opt/tools/EvtxECmd/EvtxECmd.dll"

[[ $# -lt 3 ]] && usage

cmd="$1"; shift
case "$cmd" in
  mft)
    in="$1"; out="$2"; name="${3:-mft.csv}"
    [[ -f "$MFTE" ]] || { echo "MFTECmd not found at $MFTE"; exit 127; }
    echo "[parse] MFTECmd: -f $in --csv $out --csvf $name --fl"
    dotnet "$MFTE" -f "$in" --csv "$out" --csvf "$name" --fl
    if [[ -s "$out/$name" ]]; then
      echo "✔ wrote $out/$name"
    else
      echo "✘ expected $out/$name but file missing or empty"
      exit 2
    fi
    ;;
  amcache)
    in="$1"; out="$2"; name="${3:-amcache.csv}"
    [[ -f "$AMC" ]] || { echo "AmcacheParser not found at $AMC"; exit 127; }
    echo "[parse] AmcacheParser: -i -f $in --csv $out --csvf $name"
    dotnet "$AMC" -i -f "$in" --csv "$out" --csvf "$name"
    if [[ -s "$out/$name" ]]; then
      echo "✔ wrote $out/$name"
    else
      echo "✘ expected $out/$name but file missing or empty"
      exit 2
    fi
    ;;
  evtx-dir)
    in="$1"; out="$2"; name="${3:-evtx_all.csv}"
    EVTX="/opt/tools/EvtxECmd/EvtxECmd.dll"
    [[ -f "$EVTX" ]] || { echo "EvtxECmd not found at $EVTX"; exit 127; }

    mkdir -p "$out"
    log="$out/evtxecmd.log"

    echo "[parse] Security events: parsing EVTX directory..."
    # เก็บ stdout/stderr ทั้งหมดไว้ที่ log เพื่อไม่ให้คอนโซลยาว
    if ! dotnet "$EVTX" -d "$in" --csv "$out" --csvf "$name" >"$log" 2>&1; then
      echo "✘ EvtxECmd failed (see $log)"
      # ช่วยดีบักเบื้องต้น: แสดงท้าย log สักหน่อย
      tail -n 80 "$log" || true
      exit 2
    fi

    if [[ -s "$out/$name" ]]; then
      # นับจำนวนแถว (ตัด header 1 บรรทัด)
      rows=$(($(wc -l < "$out/$name") - 1))
      [[ $rows -lt 0 ]] && rows=0
      # นับจำนวน error จากสรุปใน log ถ้ามี
      errs=$(grep -E "error count: [0-9]+" "$log" | awk '{s+=$3} END{print s+0}')
      echo "✔ wrote $out/$name  (${rows} rows, errors: ${errs:-0})"
      echo "  log: $log"
    else
      echo "✘ expected $out/$name but file missing or empty"
      echo "  See log: $log"
      exit 2
    fi
    ;;
esac
