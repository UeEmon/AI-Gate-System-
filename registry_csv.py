"""Version 1 CSV interchange for the vehicle registry."""
import csv
import io
import events

COLUMNS = ['region','category','kana','serial','vehicle_type','label','watch','enabled']
MAX_BYTES = 5 * 1024 * 1024

def safe_cell(value):
    value = str(value)
    # Protect spreadsheet users; reversible by this importer.
    return "'" + value if value.startswith(("'", '=', '+', '-', '@', '\t', '\r', '\n')) else value

def export_registry(root):
    output = io.StringIO(newline='')
    writer = csv.writer(output); writer.writerow(COLUMNS)
    with events.connection(root) as db:
        for row in db.execute('SELECT * FROM vehicles ORDER BY plate_key'):
            fields = row['plate_key'].split('|')
            writer.writerow([safe_cell(v) for v in fields + [row['vehicle_type'], row['label'], row['watch'], row['enabled']]])
    return output.getvalue().encode('utf-8-sig')

def parse_registry(payload):
    if not payload or len(payload)>MAX_BYTES: raise ValueError('CSVは空でない5MB以下のファイルにしてください。')
    try: text=payload.decode('utf-8-sig')
    except UnicodeDecodeError:
        try: text=payload.decode('cp932')
        except UnicodeDecodeError: raise ValueError('文字コードはUTF-8またはCP932にしてください。')
    if '\x00' in text: raise ValueError('CSVに使用できない文字があります。')
    reader=csv.DictReader(io.StringIO(text,newline=''),strict=True)
    try:
        if reader.fieldnames!=COLUMNS: raise ValueError('CSVの列は '+','.join(COLUMNS)+' の順にしてください。')
        items=[]; seen=set()
        for row in reader:
            line=reader.line_num
            try:
                if None in row or any(v is None for v in row.values()): raise ValueError('列数が一致しません。')
                row={k:(v[1:] if v.startswith("'") and len(v)>1 and v[1] in "'=+-@\t\r\n" else v) for k,v in row.items()}
                key=events.plate_key(row)
                if key in seen: raise ValueError('CSV内に同じナンバーが重複しています。')
                if row['vehicle_type'] not in events.TYPES: raise ValueError('車種はcar/motorcycle/bus/truckです。')
                if len(row['label'])>120: raise ValueError('登録名は120文字以下にしてください。')
                for flag in ('watch','enabled'):
                    if row[flag].strip().lower() not in ('0','1','true','false'): raise ValueError(flag+'は0/1またはtrue/falseです。')
                    row[flag]=row[flag].strip().lower() in ('1','true')
                items.append(row);seen.add(key)
                if len(items)>10000: raise ValueError('CSVは10000件以下にしてください。')
            except (ValueError,KeyError) as exc: raise ValueError(f'{line}行目: {exc}') from exc
    except csv.Error as exc: raise ValueError(f'CSV形式が不正です（{reader.line_num}行目）。') from exc
    if not items: raise ValueError('CSVに登録データがありません。')
    return items

def import_registry(root,payload,mode='add',preview=True):
    if mode not in ('add','update'): raise ValueError('取り込み方式が不正です。')
    items=parse_registry(payload)
    counts=dict(added=0,updated=0,skipped=0,total=len(items),preview=preview)
    with events.connection(root) as db:
        db.execute('BEGIN IMMEDIATE')
        for item in items:
            existing=db.execute('SELECT id FROM vehicles WHERE plate_key=?',(events.plate_key(item),)).fetchone()
            if existing and mode=='add': counts['skipped']+=1;continue
            events.register_vehicle(root,item,existing['id'] if existing else None,database=db)
            counts['updated' if existing else 'added']+=1
        if preview: db.rollback()
    return counts
