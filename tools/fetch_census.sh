#!/usr/bin/env bash
# Fetch the Census cartographic boundary files `worst-route aux places` reads
# (docs/reference/presentation.md, **Places**): the places file (1:500k) of each state named by
# its FIPS code, then the state files (1:5m for the index, 1:20m for drawing), all of the pinned
# 2025 vintage, into $WORST_ROUTE_DATA/aux/census/ (default ./data/aux/census/). Run by a person;
# never by the gate, a test or an agent:
#
#   tools/fetch_census.sh 41 53     # Oregon's and Washington's places, and the state files
#
# Each file is fetched with curl under the project's User-Agent (config.Settings.user_agent) to a
# temporary name, then checked against its pinned sha256: a match is kept, a mismatch deleted
# (exit 1). The two state files and the places of the 48 contiguous states and DC are pinned
# (`us-t1`'s states: `config.STATES` less Alaska and Hawaii). A file with no pinned sum is kept
# and its sum printed, to pin here. A failed fetch leaves nothing behind (exit 1).
set -euo pipefail

cd "$(dirname "$0")/.."

base="https://www2.census.gov/geo/tiger/GENZ2025/shp"
out="${WORST_ROUTE_DATA:-data}/aux/census"

pinned() {
  case "$1" in
    cb_2025_us_state_5m.zip) echo 8a45692bc532dbd38938a1924f445850cef2682ea67d750d7fd2f19cfe836903 ;;
    cb_2025_us_state_20m.zip) echo 9340b6d995e971b2b4230518f4fa85e6cd9e7fe6811afabc90a6a8b1191530f6 ;;
    cb_2025_01_place_500k.zip) echo 7d70098c800f959a911766b7c064a79db7dd8fc018643d8d72266e4b4eeb5334 ;;
    cb_2025_04_place_500k.zip) echo e283f98492857bdb2fa61abad7bf3cef2936634149b7d9631032a75ae1548d27 ;;
    cb_2025_05_place_500k.zip) echo a036ac4b12d45697ef677c0c9b7cdca8571a14734cee594f0089f2f6f5571a43 ;;
    cb_2025_06_place_500k.zip) echo d8aa978aa353baf70dcbf3b8d4499edf2b7f71dc8dbd33f3a6a099caf9c15607 ;;
    cb_2025_08_place_500k.zip) echo ea2574a32b092ddd0d4da836c4bc25c1bffd45f96886cc044f2eb4f2ddeeba45 ;;
    cb_2025_09_place_500k.zip) echo 614fb5643a4de82bdbb4f6154aa3ce7b516f7613ceec63003db90b605e1d5d2b ;;
    cb_2025_10_place_500k.zip) echo 01ef2f1e3a1dd9db43b946d79565abd103a246e00cc45c2fb05ddec498ae4141 ;;
    cb_2025_11_place_500k.zip) echo 1b3afac535e157af2a32e1369c0f85c6502f33ebd3a0f76a2e982e236aa7f667 ;;
    cb_2025_12_place_500k.zip) echo 21df5b04e7ec4f20aada2f36396de3bf144b4070c03561744fc47fca20c82cd5 ;;
    cb_2025_13_place_500k.zip) echo 73efa912cd615e20f9e9bacf9ca3c7b451653fdb31c41674e54cbfb85b7f97c6 ;;
    cb_2025_16_place_500k.zip) echo 48d40376c6f55533be3cdb808cedcff4fa4d48962e9c4f865d084d53bb4d71d2 ;;
    cb_2025_17_place_500k.zip) echo 3efa61259f08d3ffb2fa1f5715d40225b20070ce6f9a06c4be62731202686679 ;;
    cb_2025_18_place_500k.zip) echo 0de15b163aede2dd9e53c8e0a18dac590eb695e325e392ef0f14c0ea3cdaefee ;;
    cb_2025_19_place_500k.zip) echo eaa8df4949e5dfb687b4c47b1c6f8a784935eb0572cf4fe49b137b7b6d7bbb70 ;;
    cb_2025_20_place_500k.zip) echo e86848a1c5c8038418416fdf83093907f172294c58b6f1f99c30917bae17db4f ;;
    cb_2025_21_place_500k.zip) echo 9534b6a33fe514c24341ade1c7d1629a9ed9658d527791ed57bb96900c68d096 ;;
    cb_2025_22_place_500k.zip) echo f3e785f131c1e8bc17194439e77a36b801a1813a77088341812f0924837cdc20 ;;
    cb_2025_23_place_500k.zip) echo 5aa3e790c33aacac997f9cde8f87870fc3f91f590a81d76f41d68f07f42a93d8 ;;
    cb_2025_24_place_500k.zip) echo bd7325be4502a311976b0eb21d513691115bdf70938be1c886190e260486f139 ;;
    cb_2025_25_place_500k.zip) echo 077274c10aa4c02fbf453c26ed18e32dfe9044a4d1097a1bed9f075c70574d7a ;;
    cb_2025_26_place_500k.zip) echo 1e303569575ad524f1a4b6666912a73719f7484209e5e1ae58e5a52352445d48 ;;
    cb_2025_27_place_500k.zip) echo 01880fa53a8bbc0eb6709097ad7a77923ebde59804d97a6cf3966eb9d3d081e7 ;;
    cb_2025_28_place_500k.zip) echo b1737641eb2edfcf289ca618083f302e7f8c6a8b141101b029c29dbeb113aab5 ;;
    cb_2025_29_place_500k.zip) echo 52b7496452e9ddc86caa80b3565372bff5e223fe62878c98fa8990cc8215414c ;;
    cb_2025_30_place_500k.zip) echo cc2d5a04e86943c2c0bdec50ecd5f37995e4610ef5a6807eb7d9af8ad6d2082b ;;
    cb_2025_31_place_500k.zip) echo dc4b13af2846e19a86ec50622ac7ce6a084e7652b3c78887d8f69cf4a41e536e ;;
    cb_2025_32_place_500k.zip) echo 481234154b42916ab2e6b6e0c6c6a0b2e09fa1f39d797a3c43b66b5e3b887878 ;;
    cb_2025_33_place_500k.zip) echo fac23346255ff25986b7b0cdc092e3a5a102bb12b50de4dd8775e0354849ed4e ;;
    cb_2025_34_place_500k.zip) echo 8a7267fe90e70de9e8733c2767abe27be2a136875bf077a035e236867ba9f3d8 ;;
    cb_2025_35_place_500k.zip) echo 954f167aa8ce7c70f34a4fa9a6020c7861d1e1a7d786b3b614844c8383ee09cd ;;
    cb_2025_36_place_500k.zip) echo 6e49025177a2450ac90ad84c737f4d68cc1c73c963ab2ec0afa037454ff7b950 ;;
    cb_2025_37_place_500k.zip) echo eb00ade048577d2d441244878b06c17f1742cca06dabe66bcfa364f07ed3a0e3 ;;
    cb_2025_38_place_500k.zip) echo ecb6cd470653a73a512dc6a907fdbe1480b3c1998dc5fb6a584add03b36ec643 ;;
    cb_2025_39_place_500k.zip) echo db960dfe1150594d4ea6fdc0b5d3657e7d5a9a5407dec0ae0fc47b37ac6987a5 ;;
    cb_2025_40_place_500k.zip) echo 55adfde542bcfbb74e71919ce8aae7744902a2985e305285925eb93fcb66c922 ;;
    cb_2025_41_place_500k.zip) echo 9e06eff08882940973c5d5bbed82d433b3f4a5bb91535312186c418278eedc26 ;;
    cb_2025_42_place_500k.zip) echo 3eff47d67fc96c64a736c5a54e5329417c1cbc714b10090153a3286212804e24 ;;
    cb_2025_44_place_500k.zip) echo cfc3669090690964e1506f51f99b8b4177652feaae0c5faad48396776f1a1645 ;;
    cb_2025_45_place_500k.zip) echo 970aa10ce560bf8e02c26e10c1f6eb4b13a8fc89f9bcf18eb1976b09f541cd86 ;;
    cb_2025_46_place_500k.zip) echo 2a9da19f94cc906d07a4ff0375095ca4124a3791c99ebe862b4249a04cacaab0 ;;
    cb_2025_47_place_500k.zip) echo 35565cc3fb485c0a8a9c4b158827dcdd5c924f41041870d7227293c3321c9f77 ;;
    cb_2025_48_place_500k.zip) echo fd03650c3c6171514bfb598e2d4bda1353078c33cddc20b564e44614f24c2df2 ;;
    cb_2025_49_place_500k.zip) echo 1fcdea6e66db996a786466b3a04987ad4fd07fca64bbd5e11f88b1bfd01104a7 ;;
    cb_2025_50_place_500k.zip) echo 15cc2e3b16a81c5dd53eb111d1a107277268724825bc44c5086abd745ecd33a7 ;;
    cb_2025_51_place_500k.zip) echo d7587610d2c7e3edb9be4b542a847e5dca3df5bbb34c05af12393beb3f160ed0 ;;
    cb_2025_53_place_500k.zip) echo 24e23e4c626d87b099c45cb5cab6df90939ddc311e6b09062e16fb2436a4a7db ;;
    cb_2025_54_place_500k.zip) echo 1d46eb50e1d50eae5df2bd9785cf0310bf8700456c9cde52ad0248d0d37eb2cd ;;
    cb_2025_55_place_500k.zip) echo 529e9d472605c59baf89ddd131cd74a97799d60770bb0c29d89c7097c9a2ee61 ;;
    cb_2025_56_place_500k.zip) echo 3119cf6e95f32b5c5606956b5d2635a1ea24baae722b77160387078726cdf877 ;;
    *) echo "" ;;
  esac
}

sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    sha256sum "$1" | cut -d' ' -f1
  fi
}

names=()
for fips in "$@"; do
  if ! [[ "$fips" =~ ^[0-9][0-9]$ ]]; then
    echo "not a two-digit FIPS code: $fips" >&2
    exit 2
  fi
  names+=("cb_2025_${fips}_place_500k.zip")
done
names+=(cb_2025_us_state_5m.zip cb_2025_us_state_20m.zip)

agent="$(uv run --no-sync python -c 'from worst_route.config import settings; print(settings().user_agent)')"
mkdir -p "$out"
for name in "${names[@]}"; do
  part="$out/.$name.part"
  if ! curl -fsSL --user-agent "$agent" -o "$part" "$base/$name"; then
    rm -f "$part"
    echo "$name: the fetch failed; nothing kept" >&2
    exit 1
  fi
  sum="$(sha256 "$part")"
  want="$(pinned "$name")"
  if [ -z "$want" ]; then
    mv "$part" "$out/$name"
    echo "wrote $out/$name, sha256 $sum (no pinned sum: add it to tools/fetch_census.sh)"
  elif [ "$sum" = "$want" ]; then
    mv "$part" "$out/$name"
    echo "wrote $out/$name, sha256 verified"
  else
    rm -f "$part"
    echo "$name: sha256 $sum, expected $want; not kept" >&2
    exit 1
  fi
done
