# Bundled data for Zone 3

`public_suffix_list.dat`: the full Public Suffix List (ICANN and private sections), read offline by
`zone3/hosts.py`. Nothing fetches it at runtime.

- Source: https://publicsuffix.org/list/public_suffix_list.dat
- Version in the file: `2026-10-20_08-40-56_UTC`, commit `5dd321ffe02039e2431382a095cf80e27ff5eb94`
- Fetched: 2026-10-22, sha256 `8228e4bdac6024c058a1e67859157e468a2613b826b53de0e5cae298e1b70755`
- Licence: Mozilla Public License 2.0 (the header in the file). It is shipped unchanged.

To update: fetch the same URL, check the header's VERSION line, replace the file, update these lines.
