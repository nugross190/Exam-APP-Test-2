"""Convert the original schedule xlsx grid into the long/tidy CSV that
parsers/excel.py:parse_schedule expects.

Run as a one-off preprocessor:

    python database/schedule_parser.py <input.xlsx> <output.csv>

Defaults (when no args given) match the original behaviour:
input=schedule_sample.xlsx, output=schedule_parsed.csv (in CWD).
"""
import re
import sys

import pandas as pd

MONTH_MAP = {
    'Januari': '01', 'Februari': '02', 'Maret': '03', 'April': '04',
    'Mei': '05', 'Juni': '06', 'Juli': '07', 'Agustus': '08',
    'September': '09', 'Oktober': '10', 'November': '11', 'Desember': '12',
}


def parse_indo_date(date_str):
    if pd.isna(date_str):
        return None
    s = str(date_str).strip()
    s = re.sub(r'^.*?,\s*', '', s)
    for id_m, num_m in MONTH_MAP.items():
        s = s.replace(id_m, num_m)
    parts = s.split()
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return None


def parse_time(time_str):
    if pd.isna(time_str):
        return None, None
    s = str(time_str).strip().replace('.', ':')
    if '-' in s:
        start, end = s.split('-', 1)
        return start.strip(), end.strip()
    return None, None


def convert(input_xlsx: str, output_csv: str) -> int:
    df = pd.read_excel(input_xlsx, header=None)

    dates_row = df.iloc[0].tolist()
    times_row = df.iloc[1].tolist()

    col_metadata = []
    current_date = None
    for i in range(1, len(dates_row)):
        d = dates_row[i]
        t = times_row[i]
        if pd.notna(d):
            current_date = parse_indo_date(d)
        if pd.notna(t):
            t_start, t_end = parse_time(t)
        else:
            t_start, t_end = None, None
        col_metadata.append((current_date, t_start, t_end))

    parsed_data = []
    for idx in range(2, len(df)):
        row = df.iloc[idx]
        kelas = str(row[0]).strip()
        if not re.match(r'^(XI|X)\s*-', kelas):
            continue
        for col_idx, (date, t_start, t_end) in enumerate(col_metadata):
            subject = str(row[col_idx + 1]).strip()
            if subject and subject not in ['nan', ''] and not subject.startswith('Column'):
                if date and t_start:
                    parsed_data.append({
                        'kelas': kelas,
                        'subject': subject,
                        'date': date,
                        'time_start': t_start,
                        'time_end': t_end,
                    })

    result_df = pd.DataFrame(parsed_data)
    result_df.to_csv(output_csv, index=False)
    return len(result_df)


def main(argv: list[str]) -> int:
    input_xlsx = argv[1] if len(argv) > 1 else 'schedule_sample.xlsx'
    output_csv = argv[2] if len(argv) > 2 else 'schedule_parsed.csv'
    n = convert(input_xlsx, output_csv)
    print(f"Parsed {n} schedule entries from {input_xlsx} -> {output_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
