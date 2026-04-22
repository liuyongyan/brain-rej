#!/usr/bin/env bash
# Recreate data/ from public sources. Run from repo root.
# Total download size: ~770MB (most is Ximerakis Suppl. Tables S4-S12).

set -euo pipefail
cd "$(dirname "$0")/.."

UA="Mozilla/5.0"
mkdir -p data/{lehallier_2019,palovics_2022,ximerakis_2023,jeffries_2025,resources}

echo "[1/4] Lehallier 2019 (Nat Med) supplementary ..."
curl -sL -A "$UA" -o data/lehallier_2019/suppl_tables.xlsx \
  "https://static-content.springer.com/esm/art%3A10.1038%2Fs41591-019-0673-2/MediaObjects/41591_2019_673_MOESM3_ESM.xlsx"

echo "[2/4] Pálovics 2022 (Nature) supplementary ..."
curl -sL -A "$UA" -o data/palovics_2022/S5.xlsx \
  "https://static-content.springer.com/esm/art%3A10.1038%2Fs41586-022-04461-2/MediaObjects/41586_2022_4461_MOESM5_ESM.xlsx"

echo "[3/4] Ximerakis 2023 (Nat Aging) supplementary tables S2-S22 ..."
for i in 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22; do
  curl -sL -A "$UA" -o "data/ximerakis_2023/S${i}.xlsx" \
    "https://static-content.springer.com/esm/art%3A10.1038%2Fs43587-023-00373-6/MediaObjects/43587_2023_373_MOESM${i}_ESM.xlsx"
done

echo "[4/4] Jeffries 2025 (Nature) supplementary tables ..."
curl -sL -A "$UA" -o data/jeffries_2025/suppl_tables.zip \
  "https://static-content.springer.com/esm/art%3A10.1038%2Fs41586-025-09435-8/MediaObjects/41586_2025_9435_MOESM3_ESM.zip"
( cd data/jeffries_2025 && unzip -q -o suppl_tables.zip )

echo "[5/5] Cross-paper resources ..."
curl -sL -A "$UA" -o data/resources/omnipath_ligrec.tsv \
  "https://omnipathdb.org/interactions?datasets=ligrecextra,lrn,cellphonedb,celltalkdb,cellinker,connectomedb2020,italk,kirouac2010,baccin2019,guide2pharma,hpmr,ramilowski2015&fields=sources,references,curation_effort&genesymbols=yes&types=post_translational"
curl -sL -A "$UA" -o data/resources/hgnc_complete.txt \
  "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"

echo "done — data/ recreated. Run: python -m pipeline.loop --fresh"
