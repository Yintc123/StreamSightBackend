# JWT 驗證機制:FastAPI 與 Flask 的做法對照

同樣是攔下請求、驗證 JWT,FastAPI 與 Flask 的主力手段不同。核心差異只有一句:**FastAPI 有內建依賴注入(Depends),Flask 沒有**。本文整理兩者可用的做法、為什麼某些做法不該用,以及它們如何互相對應。相關程式碼位於 `app/api/dependencies/auth.py`、`app/core/auth/`。

## 目錄

- [核心差異:有沒有依賴注入](#核心差異有沒有依賴注入)
- [FastAPI 的三種做法](#fastapi-的三種做法)
  - [Middleware(不建議)](#middleware不建議)
  - [Decorator(不建議)](#decorator不建議)
  - [Depends(官方標準)](#depends官方標準)
- [Flask 的四種做法](#flask-的四種做法)
  - [Decorator(主流)](#decorator主流)
  - [before_request(勾子)](#before_request勾子)
  - [WSGI Middleware(底層)](#wsgi-middleware底層)
  - [Extension(實務常用)](#extension實務常用)
- [兩者角色對應](#兩者角色對應)
- [對照總表](#對照總表)

---

## 核心差異:有沒有依賴注入

- **Flask** 沒有內建依賴注入,靠**全域的 `request`、`g` 物件**在函式裡「隨手抓」需要的東西。所以它的主力是 **decorator**(`@login_required`)—— 在函式外包一層,自己去 `request` 抓 token。
- **FastAPI** 有**依賴注入(Depends)**,能把「這個 route 需要什麼」寫進**函式簽名**,由框架自動注入、自動串接資源(DB session、Redis)、自動清理、自動生成文件。所以它的主力是 **Depends**。

記住這個對應關係,後面所有差異都是它的延伸:

> FastAPI 的 `Depends` ≈ Flask 的 `decorator`(都負責「這個 route 要不要驗證」的精準控制),只是 `Depends` 更強。

---

## FastAPI 的三種做法

技術上三種都做得到,但**只有 `Depends` 該用**,另外兩種是其他框架的習慣硬套過來。

### Middleware(不建議)

用 `@app.middleware("http")` 在請求進到路由**之前**全域攔截:

```python
@app.middleware("http")
async def jwt_middleware(request: Request, call_next):
    token = request.headers.get("Authorization")
    # 驗證 token...
    response = await call_next(request)
    return response
```

問題:

- **全域生效**,會攔截每一個請求。但你通常有公開路由(`/health`、`/login`、`/docs`),得自己在 middleware 裡寫一堆「哪些路徑跳過」的判斷,醜且易漏。
- 拿不到「這個路由需要哪種權限」的細節。
- 回傳錯誤、注入相依資源(DB session)都彆扭。

👉 適合做**全站跨切面**:CORS、log、request ID、計時。**不該拿來做 JWT**。

### Decorator(不建議)

從 Flask 帶過來的習慣,在 FastAPI 裡很不順:

```python
def jwt_required(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        ...  # 問題:這裡怎麼拿到 request / DB session?
    return wrapper
```

問題:

- 裝飾器拿不到 FastAPI 注入的參數(request、session),得自己想辦法傳,破壞框架的參數解析。
- 會干擾 FastAPI 讀函式簽名(它靠簽名生成 OpenAPI、做驗證),常搞壞 `/docs`。
- FastAPI 生態幾乎沒人這樣做。

👉 **不推薦**,這是 Flask 思維套到 FastAPI 上。

### Depends(官方標準)

哪個 route 要驗證,就在**函式簽名裡宣告需求**:

```python
# app/api/dependencies/auth.py
async def get_current_user(
    token: str = Depends(oauth2_scheme),          # 自動從 Header 抓 Bearer token
    session: AsyncSession = Depends(get_session), # 要查 DB?直接注入
) -> User:
    payload = decode_jwt(token)          # 驗證簽章、過期
    user = await get_user(session, payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user
```

```python
@router.get("/me")
async def read_me(user: User = Depends(get_current_user)):  # 要驗證
    return user

@router.get("/health")
async def health():   # 不寫 Depends → 公開,不驗證
    return {"status": "ok"}
```

為什麼它最好:

- **精準控制**:要驗證的 route 才加 `Depends`,公開的不加,不用維護黑名單。
- **能注入資源**:驗證時要查 DB / Redis,直接再 `Depends(get_session)` 串接(依賴會遞迴解析)。
- **好測試**:測試時用 `app.dependency_overrides` 換掉 `get_current_user`,免造真 token。
- **自動進文件**:`/docs` 自動出現鎖頭圖示與 Authorize 按鈕。
- **可組合**:`get_current_user` → 再包一層 `get_current_admin`(檢查 role),一層層疊。

---

## Flask 的四種做法

Flask 常被以為只有 `decorator` 和 `before_request`,其實有四個層次可選。

### Decorator(主流)

```python
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not valid(request.headers.get("Authorization")):
            abort(401)
        return f(*args, **kwargs)
    return wrapper

@app.route("/me")
@login_required          # 要驗證的才加
def me():
    ...
```

👉 **精準控制**,哪個 route 要驗證就加。Flask 最主流做法,定位等同 FastAPI 的 `Depends`。

### before_request(勾子)

```python
@app.before_request
def check_auth():
    if request.path.startswith("/public"):
        return                    # 放行
    if not valid(request.headers.get("Authorization")):
        abort(401)
```

**全域攔截**,得自己寫「哪些路徑跳過」。可綁在 **Blueprint** 上縮小範圍,按模組切:

```python
bp = Blueprint("admin", __name__)

@bp.before_request           # 只攔這個 blueprint 底下的 route
def require_admin():
    ...
```

### WSGI Middleware(底層)

Flask 底下是 WSGI,可在**還沒進 Flask** 前攔截:

```python
class AuthMiddleware:
    def __init__(self, app):
        self.app = app
    def __call__(self, environ, start_response):
        # 檢查 environ 裡的 header...
        return self.app(environ, start_response)

app.wsgi_app = AuthMiddleware(app.wsgi_app)
```

👉 對應 FastAPI 的 ASGI middleware,做全站跨切面,很少拿來做細緻 JWT。

### Extension(實務常用)

實際專案通常不自己手刻,而是用套件包好的 decorator + hook:

- **Flask-JWT-Extended** —— `@jwt_required()`、token 建立/刷新。
- **Flask-Login** —— session-based 登入,`@login_required`、`current_user`。
- **Flask-Security / Authlib** —— 更完整的 OAuth / 角色權限。

---

## 兩者角色對應

| 層次 | Flask | FastAPI |
|------|-------|---------|
| 每個 route 精準控制 | **decorator**(`@login_required`) | **`Depends`** ✅ |
| 全域 / 群組攔截 | `before_request`(可綁 Blueprint) | middleware |
| 最底層 | WSGI middleware | ASGI middleware |
| 現成套件 | Flask-JWT-Extended 等 | fastapi-users 等 |

心智模型:

```
Flask  = 函式裡隨手抓全域 request/g → 主力是「在外面包一層」的 decorator
FastAPI = 把需求寫進函式簽名,框架自動注入 → 主力是 Depends
```

---

## 對照總表

| 面向 | Middleware | Decorator | Depends(FastAPI)/ Flask decorator |
|------|-----------|-----------|-------------------------------------|
| 控制粒度 | 全域,需寫黑名單 | 逐 route | 逐 route |
| 能否注入資源(DB/Redis) | 彆扭 | FastAPI:破壞簽名 / Flask:靠全域 g | ✅ 原生支援(FastAPI) |
| 影響 OpenAPI 文件 | 無 | FastAPI:常搞壞 | ✅ 自動生成(FastAPI) |
| 測試替換 | 難 | 難 | ✅ dependency_overrides |
| 適用場景 | 全站跨切面(CORS/log) | Flask 主流 / FastAPI 不建議 | FastAPI 標準做法 |

**結論**:與其說「三選一 / 兩選一」,更準確的是 —— **FastAPI 就用 `Depends`,Flask 就用 `decorator`**,兩者是彼此對應的主力;其餘做法(middleware / hook)留給全站跨切面,不拿來做細緻的 JWT 驗證。
