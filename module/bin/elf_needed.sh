#!/system/bin/sh
# ==============================================================================
# elf_needed.sh — dependency parser for Android's toybox-only userland
#
# Android ships no readelf / objdump / python, and toybox has no awk. So this
# script parses the ELF header, section headers, .dynamic and .dynstr using
# nothing but dd + od + tr + tail, and prints the *real* DT_NEEDED list.
#
# Usage:
#   elf_needed.sh <file.so>            # one lib, human readable
#   elf_needed.sh -n <file.so>         # NEEDED sonames only, one per line
#   elf_needed.sh -j <file.so>         # single line JSON-ish summary
#
# Exit: 0 ok, 2 unreadable / not ELF
# POSIX sh only (works under mksh, busybox ash, dash).
# ==============================================================================

E_N=1
E_MODE=full

case "$1" in
  -n) E_MODE=needed; shift ;;
  -j) E_MODE=json; shift ;;
esac
E_F="$1"

[ -n "$E_F" ] && [ -f "$E_F" ] || { echo "elf_needed: no such file: ${E_F:-<empty>}" >&2; exit 2; }

# ---- primitive readers -------------------------------------------------------
# decimal bytes of <off>/<len>, space separated
elf_bytes() {
  dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null \
    | od -A n -t u1 -v 2>/dev/null | tr -s ' \r\n' ' '
}

# u8 at offset (little endian)
elf_u8() {
  _v=0; _m=1
  for _b in $(elf_bytes "$1" "$2" 8); do
    _v=$(( _v + _b * _m )); _m=$(( _m * 256 ))
  done
  echo "$_v"
}

elf_u4() {
  _v=0; _m=1
  for _b in $(elf_bytes "$1" "$2" 4); do
    _v=$(( _v + _b * _m )); _m=$(( _m * 256 ))
  done
  echo "$_v"
}

elf_u2() {
  _v=0; _m=1
  for _b in $(elf_bytes "$1" "$2" 2); do
    _v=$(( _v + _b * _m )); _m=$(( _m * 256 ))
  done
  echo "$_v"
}

# 4-byte field in 32-bit ELF, 8-byte field in 64-bit ELF
elf_u8or4() {
  if [ "$ELF_CLASS" = 2 ]; then elf_u8 "$1" "$2"; else elf_u4 "$1" "$2"; fi
}

# NUL terminated string at absolute file offset
elf_str() {
  tail -c +$(( $2 + 1 )) "$1" 2>/dev/null | head -c 512 | tr '\0' '\n' | head -n 1
}

# ---- header ------------------------------------------------------------------
set -- $(elf_bytes "$E_F" 0 64)
[ $# -ge 52 ] || { echo "elf_needed: cannot read header of $E_F" >&2; exit 2; }
[ "$1" = 127 ] && [ "$2" = 69 ] && [ "$3" = 76 ] && [ "$4" = 70 ] || {
  echo "elf_needed: not an ELF file: $E_F" >&2; exit 2; }

ELF_CLASS="$5"      # 1=32-bit 2=64-bit
ELF_DATA="$6"       # 1=LE 2=BE
[ "$ELF_DATA" = 1 ] || echo "elf_needed: WARN big-endian, values unreliable" >&2

MACHINE=$(elf_u2 "$E_F" 18)
case "$MACHINE" in
  183) ARCH=aarch64 ;;
  40)  ARCH=arm ;;
  62)  ARCH=x86_64 ;;
  3)   ARCH=i386 ;;
  243) ARCH=riscv64 ;;
  *)   ARCH="machine-$MACHINE" ;;
esac

if [ "$ELF_CLASS" = 2 ]; then
  SHOFF=$(elf_u8 "$E_F" 40)
  SHENTSIZE=$(elf_u2 "$E_F" 58)
  SHNUM=$(elf_u2 "$E_F" 60)
  DYN_ENT=16
  SH_TYPE_OFF=4;  SH_OFF_OFF=24; SH_SIZE_OFF=32; SH_LINK_OFF=40
else
  SHOFF=$(elf_u4 "$E_F" 32)
  SHENTSIZE=$(elf_u2 "$E_F" 46)
  SHNUM=$(elf_u2 "$E_F" 48)
  DYN_ENT=8
  SH_TYPE_OFF=4;  SH_OFF_OFF=16; SH_SIZE_OFF=20; SH_LINK_OFF=24
fi

[ "$SHENTSIZE" -gt 0 ] 2>/dev/null || { echo "elf_needed: bad e_shentsize" >&2; exit 2; }
[ "$SHNUM" -gt 0 ] 2>/dev/null || { echo "elf_needed: no section headers (stripped?)" >&2; exit 2; }

# ---- locate .dynamic and its string table ------------------------------------
DYN_OFF=0; DYN_SIZE=0; DYN_LINK=-1
i=0
while [ "$i" -lt "$SHNUM" ]; do
  base=$(( SHOFF + i * SHENTSIZE ))
  s_type=$(elf_u4 "$E_F" $(( base + SH_TYPE_OFF )))
  if [ "$s_type" = 6 ]; then
    DYN_OFF=$(elf_u8or4 "$E_F" $(( base + SH_OFF_OFF )))
    DYN_SIZE=$(elf_u8or4 "$E_F" $(( base + SH_SIZE_OFF )))
    DYN_LINK=$(elf_u4 "$E_F" $(( base + SH_LINK_OFF )))
    break
  fi
  i=$(( i + 1 ))
done

[ "$DYN_OFF" -gt 0 ] && [ "$DYN_SIZE" -gt 0 ] || {
  echo "elf_needed: no .dynamic section: $E_F" >&2; exit 2; }
[ "$DYN_LINK" -ge 0 ] || { echo "elf_needed: .dynamic has no string table" >&2; exit 2; }

DYNSTR_BASE=$(( SHOFF + DYN_LINK * SHENTSIZE ))
DYNSTR_OFF=$(elf_u8or4 "$E_F" $(( DYNSTR_BASE + SH_OFF_OFF )))
DYNSTR_SIZE=$(elf_u8or4 "$E_F" $(( DYNSTR_BASE + SH_SIZE_OFF )))
[ "$DYNSTR_OFF" -gt 0 ] || { echo "elf_needed: bad .dynstr offset" >&2; exit 2; }

# ---- walk .dynamic -----------------------------------------------------------
SONAME=""
NEEDED=""
RUNPATH=""
RPATH=""
FLAGS=0
FLAGS1=0
TEXTREL=0
BINDNOW=0
HAS_SYMTAB=0

set -- $(elf_bytes "$E_F" "$DYN_OFF" "$DYN_SIZE")
if [ "$DYN_ENT" = 16 ]; then
  while [ $# -ge 16 ]; do
    tag=$(( ${1} + ${2}*256 + ${3}*65536 + ${4}*16777216 + ${5}*4294967296 + ${6}*1099511627776 + ${7}*281474976710656 + ${8}*72057594037927936 ))
    val=$(( ${9} + ${10}*256 + ${11}*65536 + ${12}*16777216 + ${13}*4294967296 + ${14}*1099511627776 + ${15}*281474976710656 + ${16}*72057594037927936 ))
    shift 16
    [ "$tag" = 0 ] && break
    case "$tag" in
      1)  NEEDED="$NEEDED $(elf_str "$E_F" $(( DYNSTR_OFF + val )))" ;;
      14) SONAME=$(elf_str "$E_F" $(( DYNSTR_OFF + val ))) ;;
      15) RPATH=$(elf_str "$E_F" $(( DYNSTR_OFF + val ))) ;;
      29) RUNPATH=$(elf_str "$E_F" $(( DYNSTR_OFF + val ))) ;;
      22) TEXTREL=1 ;;
      24) BINDNOW=1 ;;
      30) FLAGS=$val ;;
      1879048187) FLAGS1=$val ;;
    esac
  done
else
  while [ $# -ge 8 ]; do
    tag=$(( ${1} + ${2}*256 + ${3}*65536 + ${4}*16777216 ))
    val=$(( ${5} + ${6}*256 + ${7}*65536 + ${8}*16777216 ))
    shift 8
    [ "$tag" = 0 ] && break
    case "$tag" in
      1)  NEEDED="$NEEDED $(elf_str "$E_F" $(( DYNSTR_OFF + val )))" ;;
      14) SONAME=$(elf_str "$E_F" $(( DYNSTR_OFF + val ))) ;;
      15) RPATH=$(elf_str "$E_F" $(( DYNSTR_OFF + val ))) ;;
      29) RUNPATH=$(elf_str "$E_F" $(( DYNSTR_OFF + val ))) ;;
      22) TEXTREL=1 ;;
      24) BINDNOW=1 ;;
      30) FLAGS=$val ;;
      1879048187) FLAGS1=$val ;;
    esac
  done
fi

NEEDED_LIST=$(printf '%s\n' $NEEDED | sed '/^$/d')
NEEDED_N=$(printf '%s\n' "$NEEDED_LIST" | sed '/^$/d' | wc -l | tr -d ' ')

case "$E_MODE" in
  needed)
    printf '%s\n' "$NEEDED_LIST" | sed '/^$/d'
    ;;
  json)
    printf 'file=%s arch=%s class=%s soname=%s needed_n=%s runpath=%s needed=%s\n' \
      "$E_F" "$ARCH" "$ELF_CLASS" "${SONAME:-none}" "$NEEDED_N" "${RUNPATH:-none}" \
      "$(printf '%s' "$NEEDED_LIST" | tr '\n' ',')"
    ;;
  *)
    echo "FILE     $E_F"
    echo "ARCH     $ARCH (class $ELF_CLASS, e_machine $MACHINE)"
    echo "SIZE     $(wc -c < "$E_F" 2>/dev/null | tr -d ' ') bytes"
    echo "SONAME   ${SONAME:-<none>}"
    echo "RUNPATH  ${RUNPATH:-<none>}"
    echo "RPATH    ${RPATH:-<none>}"
    echo "FLAGS    0x$(printf '%x' "$FLAGS" 2>/dev/null || echo "$FLAGS") (TEXTREL=$TEXTREL BIND_NOW=$BINDNOW)"
    echo "FLAGS_1  0x$(printf '%x' "$FLAGS1" 2>/dev/null || echo "$FLAGS1")"
    NZ="$NEEDED_N"; [ "$NZ" = 1 ] && NZ="library" || NZ="libraries"
    echo "NEEDED   $NEEDED_N $NZ:"
    printf '%s\n' "$NEEDED_LIST" | sed '/^$/d' | sed 's/^/  - /'
    ;;
esac
exit 0
