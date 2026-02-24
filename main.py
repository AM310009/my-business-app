import streamlit as st
import pandas as pd
import sqlite3
import io
import math
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import plotly.express as px

# --- 1. データベース設定 ---
def get_connection():
    # 以前のデータがある「business.db」を優先的に開きます
    return sqlite3.connect('business.db', check_same_thread=False)

def init_db():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY, name TEXT, reg_num TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS stock (id INTEGER PRIMARY KEY, item TEXT, qty INTEGER, price INTEGER, company_id INTEGER, image BLOB)')
        c.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY, timestamp TEXT, user TEXT, action TEXT)')
        
        # JAN列と画像列を安全に追加
        columns = [column[1] for column in c.execute("PRAGMA table_info(stock)")]
        if "jan" not in columns:
            c.execute('ALTER TABLE stock ADD COLUMN jan TEXT')
        if "image" not in columns:
            c.execute('ALTER TABLE stock ADD COLUMN image BLOB')
        conn.commit()

def save_log(action):
    user = st.session_state.get("user_role", "unknown")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        conn.execute("INSERT INTO logs (timestamp, user, action) VALUES (?, ?, ?)", (now, user, action))
        conn.commit()

# --- 2. 認証機能 ---
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if not st.session_state["authenticated"]:
        st.title("🔐 ログイン")
        user = st.text_input("ユーザー名")
        pwd = st.text_input("パスワード", type="password")
        if st.button("ログイン"):
            if (user == "admin" and pwd == "admin123") or (user == "user" and pwd == "user123"):
                st.session_state["authenticated"] = True
                st.session_state["user_role"] = user
                st.rerun()
            else:
                st.error("認証失敗")
        return False
    return True

# --- 3. PDF生成（ロゴ・印影対応） ---
def generate_multi_invoice(company_name, reg_num, selected_rows, doc_type="請求書"):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    font_path = "C:/Windows/Fonts/msgothic.ttc"
    font_name = "MS-Gothic"
    try: pdfmetrics.registerFont(TTFont(font_name, font_path))
    except: font_name = "Helvetica"

    try: p.drawImage("logo.png", 420, 750, width=100, preserveAspectRatio=True, mask='auto')
    except: pass
    try: p.drawImage("stamp.png", 450, 715, width=40, preserveAspectRatio=True, mask='auto')
    except: pass

    p.setFont(font_name, 22)
    p.drawString(100, 800, f"御 {doc_type} 書")
    p.setFont(font_name, 10)
    p.drawRightString(520, 740, "株式会社 〇〇システム")
    p.drawRightString(520, 725, f"発行日: {datetime.now().strftime('%Y/%m/%d')}")
    
    p.setFont(font_name, 12)
    p.drawString(100, 760, f"宛先: {company_name} 御中")
    if doc_type == "請求書": p.drawString(100, 740, f"登録番号: {reg_num}")
    p.line(100, 730, 500, 730)

    y = 680
    subtotal = 0
    for _, row in selected_rows.iterrows():
        line_total = row['数量'] * row['単価']
        subtotal += line_total
        p.drawString(100, y, str(row['商品名']))
        p.drawString(430, y, f"￥{line_total:,}")
        y -= 20
    
    tax = math.floor(subtotal * 0.1)
    p.line(300, y, 500, y)
    p.drawString(300, y-20, f"小計: ￥{subtotal:,}")
    p.drawString(300, y-40, f"消費税: ￥{tax:,}")
    p.setFont(font_name, 16)
    p.drawString(300, y-65, f"合計: ￥{subtotal + tax:,}")
    
    p.showPage(); p.save(); buffer.seek(0)
    return buffer

# --- 4. メインアプリ本体 ---
st.set_page_config(page_title="業務改善システム", layout="wide")
init_db()

if check_password():
    role = st.session_state["user_role"]
    # サイドバーメニューの定義
    # --- サイドバーメニューの定義 ---
    menu = st.sidebar.radio("メニュー", ["📊 ダッシュボード", "📋 在庫管理・発行", "📦 商品カタログ", "📥 入庫登録", "🏢 会社マスタ", "📜 操作履歴"])

    # 1. ダッシュボード
    if menu == "📊 ダッシュボード":
        st.header("📊 購買・在庫分析ダッシュボード")
        try:
            # データベースから最新の情報を結合して取得
            # LEFT JOINを使うことで、会社未登録の商品があってもエラーを防ぎます
            query = """
                SELECT s.item, s.qty, s.price, (s.qty * s.price) as total_value, c.name as company_name 
                FROM stock s 
                LEFT JOIN companies c ON s.company_id = c.id
            """
            df_dash = pd.read_sql(query, get_connection())

            # データが1件もない場合の表示
            if df_dash.empty or len(df_dash) == 0:
                st.info("📊 まだデータがありません。「会社マスタ」で会社を登録し、「入庫登録」で商品を追加してください。")
            else:
                # 1. 概要（メトリクス）を横に並べる
                m1, m2, m3 = st.columns(3)
                m1.metric("総在庫金額", f"￥{df_dash['total_value'].sum():,}")
                m2.metric("登録商品数", f"{len(df_dash)} 品目")
                m3.metric("総在庫数", f"{int(df_dash['qty'].sum()):,} 点")

                st.divider()

                # 2. グラフ表示
                col_g1, col_g2 = st.columns(2)
                
                with col_g1:
                    # 取引先別の在庫金額シェア（円グラフ）
                    # 会社名が空の場合は「不明」に置き換え
                    df_dash['company_name'] = df_dash['company_name'].fillna("未登録・不明")
                    fig_pie = px.pie(df_dash, values='total_value', names='company_name', 
                                     title="取引先別の在庫金額比率", hole=0.4)
                    st.plotly_chart(fig_pie, use_container_width=True)

                with col_g2:
                    # 在庫金額トップ10（棒グラフ）
                    top_10 = df_dash.sort_values('total_value', ascending=False).head(10)
                    fig_bar = px.bar(top_10, x='item', y='total_value', title="在庫金額トップ10",
                                     labels={'item': '商品名', 'total_value': '金額(円)'},
                                     color='total_value', color_continuous_scale='Blues')
                    st.plotly_chart(fig_bar, use_container_width=True)
                
        except Exception as e:
            # エラーが起きたら止まらずに、原因を表示して「修復」を促す
            st.error("⚠️ ダッシュボードの読み込みに失敗しました。")
            st.warning(f"原因: {e}")
            if st.button("🔧 データベースの構造を自動修復する"):
                init_db()
                st.success("構造を更新しました。再読み込みしてください。")
                st.rerun()

    # 2. 在庫管理
    elif menu == "📋 在庫管理・発行":
        st.header("📋 在庫管理")
        # （中略：在庫管理のコード）
        try:
            # データの取得（JANや会社名を含めて結合）
            query = """
                SELECT s.item, s.qty, s.price, (s.qty * s.price) as total_value, c.name as company_name 
                FROM stock s 
                LEFT JOIN companies c ON s.company_id = c.id
            """
            df_dash = pd.read_sql(query, get_connection())

            if df_dash.empty or df_dash['item'].isnull().all():
                st.info("表示できるデータがまだありません。「会社マスタ」と「入庫登録」を完了させてください。")
            else:
                # 1. 概要（メトリクス）
                m1, m2, m3 = st.columns(3)
                m1.metric("総在庫金額", f"￥{df_dash['total_value'].sum():,}")
                m2.metric("登録商品数", f"{len(df_dash)} 件")
                m3.metric("総数量", f"{int(df_dash['qty'].sum()):,} 点")

                st.divider()

                # 2. グラフ表示
                g1, g2 = st.columns(2)
                
                with g1:
                    # 取引先別の在庫比率
                    # 会社名が空(None)の場合は「未設定」として表示
                    df_dash['company_name'] = df_dash['company_name'].fillna("未設定")
                    fig_pie = px.pie(df_dash, values='total_value', names='company_name', 
                                     title="取引先別の在庫金額シェア", hole=0.4)
                    st.plotly_chart(fig_pie, use_container_width=True)

                with g2:
                    # 在庫金額トップ10
                    top_10 = df_dash.sort_values('total_value', ascending=False).head(10)
                    fig_bar = px.bar(top_10, x='item', y='total_value', title="在庫金額トップ10",
                                     labels={'item': '商品名', 'total_value': '金額'})
                    st.plotly_chart(fig_bar, use_container_width=True)

        except Exception as e:
            # 万が一エラーが起きても、ここで止めてエラー内容を表示する
            st.error("ダッシュボードの集計中にエラーが発生しました。")
            st.write(f"エラー詳細: {e}")
            if st.button("データベースの不整合を修復する"):
                init_db()
                st.rerun()
        # 最安値を勝ち取っている社数や商品数を集計
        query = """
            SELECT c.name as company_name, COUNT(s.id) as item_count, SUM(s.qty * s.price) as total_value
            FROM stock s 
            JOIN companies c ON s.company_id = c.id
            GROUP BY c.name
        """
        try:
            df_dash = pd.read_sql(query, get_connection())
            if not df_dash.empty:
                # メトリクス表示
                c1, c2, c3 = st.columns(3)
                c1.metric("登録商品総数", f"{df_dash['item_count'].sum()} 品目")
                c2.metric("最安値シェア1位", df_dash.loc[df_dash['item_count'].idxmax(), 'company_name'])
                c3.metric("総資産価値", f"￥{df_dash['total_value'].sum():,}")

                # グラフ表示
                col_left, col_right = st.columns(2)
                with col_left:
                    # 社別の最安値採用数
                    fig1 = px.bar(df_dash, x='company_name', y='item_count', title="取引先別の最安値採用数", labels={'item_count':'採用数', 'company_name':'取引先'})
                    st.plotly_chart(fig1, use_container_width=True)
                with col_right:
                    # 社別の在庫金額比率
                    fig2 = px.pie(df_dash, values='total_value', names='company_name', title="取引先別の在庫金額シェア", hole=0.4)
                    st.plotly_chart(fig2, use_container_width=True)
            else:
                st.info("データがありません。入庫登録からCSVを読み込んでください。")
        except:
            st.warning("集計データが不足しています。")

    # 3. 商品カタログ
    elif menu == "📦 商品カタログ":
        st.header("📦 取扱商品カタログ")
        # --- 📦 商品カタログセクション（JAN・在庫0対応版） ---
    elif menu == "📦 商品カタログ":
        st.header("📦 取扱商品カタログ")
        
        # データベースから最新の商品情報を取得
        # JANコードごとにグループ化し、一番安い価格を表示するように調整
        query = """
            SELECT jan, item, MIN(price) as min_price, SUM(qty) as total_qty, image 
            FROM stock 
            GROUP BY jan, item
        """
        try:
            df_cat = pd.read_sql(query, get_connection())
            
            if df_cat.empty:
                st.info("カタログに表示する商品がありません。先に「入庫登録」を行ってください。")
            else:
                # 検索機能
                search = st.text_input("🔍 商品名やJANコードで検索", "")
                if search:
                    df_cat = df_cat[
                        df_cat['item'].str.contains(search, case=False) | 
                        df_cat['jan'].astype(str).str.contains(search)
                    ]

                # グリッド表示（1行に4枚）
                cols = st.columns(4)
                for i, row in df_cat.iterrows():
                    with cols[i % 4]:
                        with st.container(border=True):
                            # 画像の表示（なければダミー）
                            if row['image']:
                                st.image(row['image'], use_container_width=True)
                            else:
                                st.image("https://via.placeholder.com/150?text=No+Image", use_container_width=True)
                            
                            st.write(f"**{row['item']}**")
                            st.caption(f"JAN: {row['jan']}")
                            st.write(f"最安値: <span style='color:red; font-weight:bold;'>￥{row['min_price']:,}</span>", unsafe_allow_html=True)
                            
                            # 在庫状況によるラベル切り替え
                            if row['total_qty'] <= 0:
                                st.error("❌ 在庫切れ (入荷待ち)")
                            else:
                                st.success(f"在庫あり: {int(row['total_qty'])}個")
        except Exception as e:
            st.error("カタログの表示中にエラーが発生しました。")
            st.write(f"詳細: {e}")
            if st.button("データベース構造を再確認する"):
                init_db()
                st.rerun()

    # 4. 入庫登録（ここで「在庫0」を許可します）
    elif menu == "📥 入庫登録":
        st.header("📥 入庫登録 (スマホ対応スキャン)")
        
        # ライブラリを読み込み（入庫画面の時だけ呼び出す）
        from streamlit_barcode_reader import streamlit_barcode_reader
        
        comps = pd.read_sql("SELECT id, name FROM companies", get_connection())
        
        # --- 新機能：カメラスキャン ---
        st.subheader("📸 バーコードスキャン")
        # カメラを起動してバーコードを読み取る
        barcode_data = streamlit_barcode_reader()
        
        if barcode_data:
            st.success(f"読み取り成功: {barcode_data}")
            # 読み取ったJANを初期値としてセット
            jan_input = barcode_data
        else:
            jan_input = ""

        st.divider()

        # --- 手入力フォーム（スキャン結果を反映） ---
        with st.form("in_f"):
            st.subheader("商品情報入力")
            name = st.text_input("商品名")
            jan = st.text_input("JANコード", value=jan_input) # スキャン結果が入る
            qty = st.number_input("数量", min_value=0, value=1)
            prc = st.number_input("単価 (最安値チェック対象)", min_value=0, value=0)
            target_c = st.selectbox("取引先", comps['name']) if not comps.empty else None
            img = st.file_uploader("商品写真 (任意)", type=['jpg', 'png', 'jpeg'])
            
            if st.form_submit_button("登録を実行"):
                if name and target_c and jan:
                    c_id = int(comps[comps['name'] == target_c]['id'].values[0])
                    img_bin = img.read() if img else None
                    
                    with get_connection() as conn:
                        # 既存の同一JANで高い価格のものがあれば削除（最安値維持ロジック）
                        existing = pd.read_sql("SELECT price FROM stock WHERE jan=?", conn, params=(jan,))
                        if not existing.empty:
                            if prc < existing['price'].values[0]:
                                conn.execute("DELETE FROM stock WHERE jan=?", (jan,))
                                conn.execute("INSERT INTO stock (jan, item, qty, price, company_id, image) VALUES (?,?,?,?,?,?)",
                                             (jan, name, qty, prc, c_id, img_bin))
                                st.success("最安値が更新されました！")
                            else:
                                st.warning("既存の価格の方が安いため、登録をスキップしました。")
                        else:
                            conn.execute("INSERT INTO stock (jan, item, qty, price, company_id, image) VALUES (?,?,?,?,?,?)",
                                         (jan, name, qty, prc, c_id, img_bin))
                            st.success(f"「{name}」を新規登録しました")
                        conn.commit()
                else:
                    st.error("商品名、JAN、取引先は必須です")

        # --- B. CSVから一括登録 (ここが新機能！) ---
        # --- CSVから一括登録（最安値自動選択ロジック付き） ---
        st.subheader("📁 CSVから一括最安値登録")
        st.info("CSV形式: [JAN, 商品名, 数量, 単価, 取引先名]")
        csv_file = st.file_uploader("CSVをアップロード", type=['csv'])
        
        if csv_file:
            try:
                # 文字コード対応
                try: df_csv = pd.read_csv(csv_file, encoding='shift-jis')
                except: df_csv = pd.read_csv(csv_file, encoding='utf-8')
                
                if st.button("重複チェックして最安値を登録"):
                    # 1. まずCSV内での重複を整理（JANごとに一番安い行だけ残す）
                    df_csv = df_csv.sort_values('単価').drop_duplicates(subset=['JAN'], keep='first')
                    
                    success_count = 0
                    with get_connection() as conn:
                        for _, row in df_csv.iterrows():
                            # 2. 既存のDBに同じJANがあるか確認
                            existing = pd.read_sql("SELECT id, price FROM stock WHERE jan=?", conn, params=(str(row['JAN']),))
                            
                            target_comp = comps[comps['name'] == row['取引先名']]
                            if not target_comp.empty:
                                c_id = int(target_comp['id'].values[0])
                                
                                if not existing.empty:
                                    # 既存より安い場合のみ入れ替え（既存を削除して新規登録）
                                    if row['単価'] < existing['price'].values[0]:
                                        conn.execute("DELETE FROM stock WHERE jan=?", (str(row['JAN']),))
                                        conn.execute("INSERT INTO stock (jan, item, qty, price, company_id) VALUES (?,?,?,?,?)",
                                                     (str(row['JAN']), row['商品名'], row['数量'], row['単価'], c_id))
                                        success_count += 1
                                else:
                                    # 新規JANならそのまま登録
                                    conn.execute("INSERT INTO stock (jan, item, qty, price, company_id) VALUES (?,?,?,?,?)",
                                                 (str(row['JAN']), row['商品名'], row['数量'], row['単価'], c_id))
                                    success_count += 1
                        conn.commit()
                    st.success(f"処理完了！ {success_count}件の最安値を維持/更新しました。")
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

    # 5. 会社マスタ
    elif menu == "🏢 会社マスタ":
        st.header("🏢 取引先（マスター）管理")
        
        # 1. 会社登録フォーム
        with st.form("company_form"):
            st.subheader("新規取引先の追加")
            new_name = st.text_input("取引先名（CSVの表記と完全に一致させてください）")
            new_reg = st.text_input("インボイス登録番号 (例: T1234567890123)")
            
            if st.form_submit_button("取引先を登録"):
                if new_name:
                    with get_connection() as conn:
                        conn.execute("INSERT INTO companies (name, reg_num) VALUES (?, ?)", (new_name, new_reg))
                        conn.commit()
                    st.success(f"「{new_name}」を登録しました。")
                    st.rerun()
                else:
                    st.error("会社名を入力してください。")

        st.divider()
        
        # 2. 登録済みリストの表示と削除
        st.subheader("📋 登録済み取引先一覧")
        df_comps = pd.read_sql("SELECT id, name, reg_num FROM companies", get_connection())
        
        if not df_comps.empty:
            # テーブル表示
            st.dataframe(df_comps, hide_index=True, use_container_width=True)
            
            # 削除機能（管理者のみ等、必要に応じて制限可能）
            with st.expander("🗑️ 取引先の削除"):
                del_id = st.number_input("削除する会社のIDを入力してください", min_value=1, step=1)
                if st.button("指定した会社を完全に削除する", type="primary"):
                    with get_connection() as conn:
                        conn.execute("DELETE FROM companies WHERE id=?", (del_id,))
                        conn.commit()
                    save_log(f"取引先削除: ID {del_id}")
                    st.success("削除が完了しました。")
                    st.rerun()
        else:
            st.info("登録されている取引先はありません。")

    # --- 📜 操作履歴セクション ---
    elif menu == "📜 操作履歴":
        st.header("📜 操作履歴")
        if role == "admin":
            try:
                logs = pd.read_sql("SELECT timestamp as 日時, user as ユーザー, action as 操作内容 FROM logs ORDER BY id DESC LIMIT 100", get_connection())
                st.table(logs)
            except:
                st.info("履歴データがまだありません。")
        else:
            st.warning("管理者権限（admin）が必要です。")

    # --- 万が一の時の else（メニューがどれにも当てはまらない場合） ---
    else:
        st.write("メニューを選択してください。")