import json, os, sqlite3, threading
from datetime import datetime
from functools import wraps
import paho.mqtt.client as mqtt
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'gate_system.db')
REQ_TOPIC = 'community/gate/request'
RESP_TOPIC = 'community/gate/response'
app = Flask(__name__)
app.secret_key = 'rfid-community-gate-secret-key'
mqtt_status = {'connected': False, 'message': '未连接'}

def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def uid(v): return (v or '').replace(' ', '').upper()
def db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c
def one(sql, p=()):
    c=db(); r=c.execute(sql,p).fetchone(); c.close(); return r
def all_rows(sql, p=()):
    c=db(); r=c.execute(sql,p).fetchall(); c.close(); return r
def exec_sql(sql, p=()):
    c=db(); cur=c.cursor(); cur.execute(sql,p); c.commit(); i=cur.lastrowid; c.close(); return i

def cols(cur, name):
    cur.execute(f'PRAGMA table_info({name})')
    return [r[1] for r in cur.fetchall()]

def init_db():
    c=db(); cur=c.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS Users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, created_at TEXT)')
    cur.execute('CREATE TABLE IF NOT EXISTS Owners (owner_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_name TEXT NOT NULL, phone TEXT, address TEXT, remark TEXT, created_at TEXT)')
    cur.execute('CREATE TABLE IF NOT EXISTS Vehicles (vehicle_id INTEGER PRIMARY KEY AUTOINCREMENT, plate_number TEXT UNIQUE NOT NULL, owner_id INTEGER, card_uid TEXT UNIQUE NOT NULL, vehicle_type TEXT, status INTEGER DEFAULT 1, expire_date TEXT, created_at TEXT)')
    if 'vehicle_id' not in cols(cur, 'Vehicles'):
        cur.execute('ALTER TABLE Vehicles RENAME TO Vehicles_old')
        cur.execute('CREATE TABLE Vehicles (vehicle_id INTEGER PRIMARY KEY AUTOINCREMENT, plate_number TEXT UNIQUE NOT NULL, owner_id INTEGER, card_uid TEXT UNIQUE NOT NULL, vehicle_type TEXT, status INTEGER DEFAULT 1, expire_date TEXT, created_at TEXT)')
        for r in cur.execute('SELECT plate_number, owner_name, card_uid, status, expire_date FROM Vehicles_old').fetchall():
            cur.execute('SELECT owner_id FROM Owners WHERE owner_name=?', (r[1],)); o=cur.fetchone()
            if o: oid=o[0]
            else:
                cur.execute('INSERT INTO Owners (owner_name, phone, address, remark, created_at) VALUES (?, ?, ?, ?, ?)', (r[1], '', '', '原数据迁移', now())); oid=cur.lastrowid
            cur.execute('INSERT OR IGNORE INTO Vehicles (plate_number, owner_id, card_uid, vehicle_type, status, expire_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (r[0], oid, uid(r[2]), '小型汽车', r[3], r[4], now()))
        cur.execute('DROP TABLE Vehicles_old')
    cur.execute('CREATE TABLE IF NOT EXISTS AccessLogs (log_id INTEGER PRIMARY KEY AUTOINCREMENT, card_uid TEXT, plate_number TEXT, owner_name TEXT, access_type TEXT, access_result TEXT, reason TEXT, access_time TEXT)')
    if cur.execute('SELECT COUNT(*) FROM Users').fetchone()[0] == 0:
        cur.execute('INSERT INTO Users (username, password, created_at) VALUES (?, ?, ?)', ('admin', generate_password_hash('123456'), now()))
    if cur.execute('SELECT COUNT(*) FROM Owners').fetchone()[0] == 0 and cur.execute('SELECT COUNT(*) FROM Vehicles').fetchone()[0] == 0:
        seeds=[('张三','浙A·88888','F1CD2203',1,'2028-12-31'),('李四','沪A·66666','AF786BDB',1,'2028-12-31'),('王五','京A·00001','7FE0C3DB',1,'2028-12-31'),('赵六','粤B·12345','9F7865DB',0,'2028-12-31'),('钱七','苏E·54321','EF27A2DB',1,'2025-01-01')]
        for n,p,u,s,e in seeds:
            cur.execute('INSERT INTO Owners (owner_name, phone, address, remark, created_at) VALUES (?, ?, ?, ?, ?)', (n,'','','初始测试车主',now()))
            cur.execute('INSERT INTO Vehicles (plate_number, owner_id, card_uid, vehicle_type, status, expire_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (p,cur.lastrowid,u,'小型汽车',s,e,now()))
    c.commit(); c.close()

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        return f(*a, **k) if session.get('user_id') else redirect(url_for('login'))
    return w

def log_access(card, plate, owner, result, reason):
    exec_sql('INSERT INTO AccessLogs (card_uid, plate_number, owner_name, access_type, access_result, reason, access_time) VALUES (?, ?, ?, ?, ?, ?, ?)', (card, plate, owner, '入口', result, reason, now()))

def judge_card(card):
    v=one('SELECT v.*, o.owner_name FROM Vehicles v LEFT JOIN Owners o ON v.owner_id=o.owner_id WHERE v.card_uid=?', (card,))
    if not v:
        log_access(card, 'None', 'Unknown', 'DENIED', 'Card Not Found')
        return {'status':'DENIED','owner':'Unknown','plate':'None','msg':'Card Not Found'}
    owner=v['owner_name'] or 'Unknown'; plate=v['plate_number']; exp=v['expire_date'] or '1970-01-01'
    try: expired=datetime.strptime(exp, '%Y-%m-%d') < datetime.now()
    except ValueError: expired=True
    if int(v['status']) == 0:
        log_access(card, plate, owner, 'DENIED', 'Card Revoked'); return {'status':'DENIED','owner':owner,'plate':plate,'msg':'Card Revoked'}
    if expired:
        log_access(card, plate, owner, 'DENIED', 'Card Expired'); return {'status':'DENIED','owner':owner,'plate':plate,'msg':'Card Expired'}
    log_access(card, plate, owner, 'APPROVED', 'Welcome Home'); return {'status':'APPROVED','owner':owner,'plate':plate,'msg':'Welcome Home'}

def on_connect(client, userdata, flags, rc):
    mqtt_status.update({'connected': rc == 0, 'message': '已连接 EMQX 云平台' if rc == 0 else f'连接失败：{rc}'})
    if rc == 0: client.subscribe(REQ_TOPIC)
def on_disconnect(client, userdata, rc): mqtt_status.update({'connected': False, 'message': 'MQTT 连接已断开'})
def on_message(client, userdata, msg):
    try:
        card=uid(json.loads(msg.payload.decode()).get('card_uid',''))
        resp=judge_card(card)
        client.publish(RESP_TOPIC, json.dumps(resp, ensure_ascii=False))
    except Exception as e: print('MQTT处理失败:', e)
def start_mqtt():
    def run():
        try:
            client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION1); client.on_connect=on_connect; client.on_disconnect=on_disconnect; client.on_message=on_message
            client.connect('broker.emqx.io', 1883, 60); client.loop_forever()
        except Exception as e: mqtt_status.update({'connected': False, 'message': f'MQTT 启动失败：{e}'})
    threading.Thread(target=run, daemon=True).start()

@app.context_processor
def inject(): return {'mqtt_status': mqtt_status}
@app.route('/')
def index(): return redirect(url_for('dashboard' if session.get('user_id') else 'login'))
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        u=one('SELECT * FROM Users WHERE username=?', (request.form.get('username','').strip(),))
        if u and check_password_hash(u['password'], request.form.get('password','')):
            session['user_id']=u['user_id']; session['username']=u['username']; return redirect(url_for('dashboard'))
        flash('用户名或密码错误','danger')
    return render_template('login.html')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))
@app.route('/dashboard')
@login_required
def dashboard():
    today=datetime.now().strftime('%Y-%m-%d')+'%'
    stats={'owners':one('SELECT COUNT(*) c FROM Owners')['c'],'vehicles':one('SELECT COUNT(*) c FROM Vehicles')['c'],'normal':one('SELECT COUNT(*) c FROM Vehicles WHERE status=1')['c'],'revoked':one('SELECT COUNT(*) c FROM Vehicles WHERE status=0')['c'],'today':one('SELECT COUNT(*) c FROM AccessLogs WHERE access_time LIKE ?', (today,))['c'],'denied':one("SELECT COUNT(*) c FROM AccessLogs WHERE access_result='DENIED'")['c']}
    return render_template('dashboard.html', stats=stats, logs=all_rows('SELECT * FROM AccessLogs ORDER BY access_time DESC LIMIT 8'))
@app.route('/owners')
@login_required
def owners():
    k=request.args.get('keyword','').strip(); like=f'%{k}%'
    rows=all_rows('SELECT * FROM Owners WHERE owner_name LIKE ? OR phone LIKE ? OR address LIKE ? ORDER BY owner_id DESC',(like,like,like)) if k else all_rows('SELECT * FROM Owners ORDER BY owner_id DESC')
    return render_template('owners.html', rows=rows, keyword=k)
@app.route('/owners/add', methods=['GET','POST'])
@login_required
def owner_add():
    if request.method == 'POST':
        exec_sql('INSERT INTO Owners (owner_name, phone, address, remark, created_at) VALUES (?, ?, ?, ?, ?)', (request.form['owner_name'].strip(), request.form.get('phone','').strip(), request.form.get('address','').strip(), request.form.get('remark','').strip(), now()))
        flash('车主信息添加成功', 'success'); return redirect(url_for('owners'))
    return render_template('owner_form.html', row=None)

@app.route('/owners/edit/<int:id>', methods=['GET','POST'])
@login_required
def owner_edit(id):
    row=one('SELECT * FROM Owners WHERE owner_id=?',(id,))
    if not row: flash('车主不存在','danger'); return redirect(url_for('owners'))
    if request.method == 'POST':
        exec_sql('UPDATE Owners SET owner_name=?, phone=?, address=?, remark=? WHERE owner_id=?', (request.form['owner_name'].strip(), request.form.get('phone','').strip(), request.form.get('address','').strip(), request.form.get('remark','').strip(), id))
        flash('车主信息修改成功','success'); return redirect(url_for('owners'))
    return render_template('owner_form.html', row=row)

@app.route('/owners/delete/<int:id>', methods=['POST'])
@login_required
def owner_delete(id):
    if one('SELECT COUNT(*) c FROM Vehicles WHERE owner_id=?',(id,))['c']:
        flash('该车主名下仍有车辆，不能删除','warning')
    else:
        exec_sql('DELETE FROM Owners WHERE owner_id=?',(id,)); flash('车主信息删除成功','success')
    return redirect(url_for('owners'))

@app.route('/vehicles')
@login_required
def vehicles():
    k=request.args.get('keyword','').strip(); like=f'%{k}%'
    sql='SELECT v.*, o.owner_name FROM Vehicles v LEFT JOIN Owners o ON v.owner_id=o.owner_id'
    rows=all_rows(sql+' WHERE v.plate_number LIKE ? OR v.card_uid LIKE ? OR o.owner_name LIKE ? ORDER BY v.vehicle_id DESC',(like,like,like)) if k else all_rows(sql+' ORDER BY v.vehicle_id DESC')
    return render_template('vehicles.html', rows=rows, keyword=k, today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/vehicles/add', methods=['GET','POST'])
@login_required
def vehicle_add():
    owners_list=all_rows('SELECT * FROM Owners ORDER BY owner_id DESC')
    if request.method == 'POST':
        try:
            exec_sql('INSERT INTO Vehicles (plate_number, owner_id, card_uid, vehicle_type, status, expire_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (request.form['plate_number'].strip(), request.form.get('owner_id'), uid(request.form['card_uid']), request.form.get('vehicle_type','小型汽车').strip(), int(request.form.get('status',1)), request.form.get('expire_date','').strip(), now()))
            flash('车辆信息添加成功','success'); return redirect(url_for('vehicles'))
        except sqlite3.IntegrityError: flash('车牌号或 RFID 卡号已存在','danger')
    return render_template('vehicle_form.html', row=None, owners=owners_list)

@app.route('/vehicles/edit/<int:id>', methods=['GET','POST'])
@login_required
def vehicle_edit(id):
    row=one('SELECT * FROM Vehicles WHERE vehicle_id=?',(id,)); owners_list=all_rows('SELECT * FROM Owners ORDER BY owner_id DESC')
    if not row: flash('车辆不存在','danger'); return redirect(url_for('vehicles'))
    if request.method == 'POST':
        try:
            exec_sql('UPDATE Vehicles SET plate_number=?, owner_id=?, card_uid=?, vehicle_type=?, status=?, expire_date=? WHERE vehicle_id=?', (request.form['plate_number'].strip(), request.form.get('owner_id'), uid(request.form['card_uid']), request.form.get('vehicle_type','小型汽车').strip(), int(request.form.get('status',1)), request.form.get('expire_date','').strip(), id))
            flash('车辆信息修改成功','success'); return redirect(url_for('vehicles'))
        except sqlite3.IntegrityError: flash('车牌号或 RFID 卡号已存在','danger')
    return render_template('vehicle_form.html', row=row, owners=owners_list)

@app.route('/vehicles/delete/<int:id>', methods=['POST'])
@login_required
def vehicle_delete(id): exec_sql('DELETE FROM Vehicles WHERE vehicle_id=?',(id,)); flash('车辆信息删除成功','success'); return redirect(url_for('vehicles'))

@app.route('/access-logs')
@login_required
def access_logs():
    k=request.args.get('keyword','').strip(); result=request.args.get('result','').strip(); start=request.args.get('start_date','').strip(); end=request.args.get('end_date','').strip()
    cond=[]; ps=[]
    if k: cond.append('(card_uid LIKE ? OR plate_number LIKE ? OR owner_name LIKE ? OR reason LIKE ?)'); ps += [f'%{k}%']*4
    if result: cond.append('access_result=?'); ps.append(result)
    if start: cond.append('access_time>=?'); ps.append(start+' 00:00:00')
    if end: cond.append('access_time<=?'); ps.append(end+' 23:59:59')
    sql='SELECT * FROM AccessLogs' + ((' WHERE ' + ' AND '.join(cond)) if cond else '') + ' ORDER BY access_time DESC'
    return render_template('access_logs.html', rows=all_rows(sql, tuple(ps)), keyword=k, result=result, start_date=start, end_date=end)

@app.route('/access-logs/add', methods=['GET','POST'])
@login_required
def access_log_add():
    if request.method == 'POST':
        exec_sql('INSERT INTO AccessLogs (card_uid, plate_number, owner_name, access_type, access_result, reason, access_time) VALUES (?, ?, ?, ?, ?, ?, ?)', (uid(request.form.get('card_uid','')), request.form.get('plate_number','').strip(), request.form.get('owner_name','').strip(), request.form.get('access_type','入口'), request.form.get('access_result','APPROVED'), request.form.get('reason','').strip(), request.form.get('access_time','').strip() or now()))
        flash('出入记录添加成功','success'); return redirect(url_for('access_logs'))
    return render_template('access_log_form.html', row=None, now=now())

@app.route('/access-logs/edit/<int:id>', methods=['GET','POST'])
@login_required
def access_log_edit(id):
    row=one('SELECT * FROM AccessLogs WHERE log_id=?',(id,))
    if not row: flash('出入记录不存在','danger'); return redirect(url_for('access_logs'))
    if request.method == 'POST':
        exec_sql('UPDATE AccessLogs SET card_uid=?, plate_number=?, owner_name=?, access_type=?, access_result=?, reason=?, access_time=? WHERE log_id=?', (uid(request.form.get('card_uid','')), request.form.get('plate_number','').strip(), request.form.get('owner_name','').strip(), request.form.get('access_type','入口'), request.form.get('access_result','APPROVED'), request.form.get('reason','').strip(), request.form.get('access_time','').strip(), id))
        flash('出入记录修改成功','success'); return redirect(url_for('access_logs'))
    return render_template('access_log_form.html', row=row, now=now())

@app.route('/access-logs/delete/<int:id>', methods=['POST'])
@login_required
def access_log_delete(id): exec_sql('DELETE FROM AccessLogs WHERE log_id=?',(id,)); flash('出入记录删除成功','success'); return redirect(url_for('access_logs'))

@app.route('/search')
@login_required
def search():
    k=request.args.get('keyword','').strip(); o=[]; v=[]; l=[]
    if k:
        like=f'%{k}%'; o=all_rows('SELECT * FROM Owners WHERE owner_name LIKE ? OR phone LIKE ? OR address LIKE ?',(like,like,like))
        v=all_rows('SELECT v.*, o.owner_name FROM Vehicles v LEFT JOIN Owners o ON v.owner_id=o.owner_id WHERE v.plate_number LIKE ? OR v.card_uid LIKE ? OR o.owner_name LIKE ?',(like,like,like))
        l=all_rows('SELECT * FROM AccessLogs WHERE card_uid LIKE ? OR plate_number LIKE ? OR owner_name LIKE ? OR reason LIKE ? ORDER BY access_time DESC',(like,like,like,like))
    return render_template('search.html', keyword=k, owners=o, vehicles=v, logs=l)

@app.route('/settings/password', methods=['GET','POST'])
@login_required
def password():
    if request.method == 'POST':
        user=one('SELECT * FROM Users WHERE user_id=?',(session['user_id'],)); old=request.form.get('old_password',''); new=request.form.get('new_password',''); confirm=request.form.get('confirm_password','')
        if not check_password_hash(user['password'], old): flash('原密码错误','danger')
        elif len(new) < 6: flash('新密码长度不能少于 6 位','warning')
        elif new != confirm: flash('两次输入的新密码不一致','warning')
        else:
            exec_sql('UPDATE Users SET password=? WHERE user_id=?',(generate_password_hash(new),session['user_id'])); session.clear(); flash('密码修改成功，请重新登录','success'); return redirect(url_for('login'))
    return render_template('password.html')

if __name__ == '__main__':
    init_db(); start_mqtt(); app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
