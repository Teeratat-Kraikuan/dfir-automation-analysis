#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  parse mft <input_MFT> <out_dir> [csv_name]"
  echo "  parse amcache <input_Amcache.hve> <out_dir> [csv_name]"
  exit 1
}

[[ $# -lt 3 ]] && usage

cmd="$1"; shift
case "$cmd" in
  mft)
    in="$1"; out="$2"; name="${3:-mft.csv}"
    dotnet /opt/tools/MFTECmd/MFTECmd.dll -f "$in" --csv "$out" --csvf "$name" --fl
    echo "✔ wrote $out/$name"
    ;;
  amcache)
    in="$1"; out="$2"; name="${3:-amcache.csv}"
    dotnet /opt/tools/AmcacheParser/AmcacheParser.dll -i -f "$in" --csv "$out" --csvf "$name"
    echo "✔ wrote $out/$name"
    ;;
  *)
    usage
    ;;
esac