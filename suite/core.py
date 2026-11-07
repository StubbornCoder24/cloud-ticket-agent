# 基础设施：配置从环境变量读取，Store管理SQLite，Provider封装阿里云请求。
# 业务规则放在rag/agent中；这里统一处理连接、输入校验、用量与错误，不输出密钥。

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent

# 本地向量/重排模型的缓存：一个进程只加载一次，避免每次请求重读几百 MB 权重
_LOCAL_MODELS = {}
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
# sentence-transformers 是可选依赖：没装时整体退回哈希向量/API，而不是让请求直接500。
_LOCAL_ST_AVAILABLE = None


def _local_st_available():
    global _LOCAL_ST_AVAILABLE
    if _LOCAL_ST_AVAILABLE is None:
        try:
            import sentence_transformers  # noqa: F401
            _LOCAL_ST_AVAILABLE = True
        except Exception:
            _LOCAL_ST_AVAILABLE = False
    return _LOCAL_ST_AVAILABLE


def local_embedding_info(path):
    """判断本地 embedding 是否可用，返回 (模型名, 维度)；不可用返回 None。

    目录存在但没装 sentence-transformers 时也返回 None，保证 backend 标识与实际
    生成的向量一致，避免出现“标记本地模型、实际写入哈希向量”的混用。
    """
    if not path or not _local_st_available():
        return None
    config_file = Path(path) / "config.json"
    if not config_file.exists():
        return None
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
        dim = int(data.get("hidden_size") or 0)
        return (Path(path).name, dim) if dim else None
    except (OSError, ValueError):
        return None

# 业务可解释错误：携带用户可见信息与HTTP状态，供接口统一返回。
class AppError(Exception):
    # 保留可解释错误消息及HTTP状态，由接口处理器决定响应形式。
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# 真正加载模型时才 import：依赖缺失的情况已由 _local_st_available 提前拦掉，
# 这里只剩“模型文件损坏/路径不可读”等真实加载错误，按业务错误上报而不是500。
def _load_sentence_transformer(path):
    if path not in _LOCAL_MODELS:
        from sentence_transformers import SentenceTransformer

        try:
            _LOCAL_MODELS[path] = SentenceTransformer(str(path), device="cpu")
        except Exception as error:
            raise AppError("本地Embedding模型加载失败：" + str(error)[:200], 500) from None
    return _LOCAL_MODELS[path]


def _load_cross_encoder(path):
    if path not in _LOCAL_MODELS:
        from sentence_transformers import CrossEncoder

        try:
            _LOCAL_MODELS[path] = CrossEncoder(str(path), max_length=512, device="cpu")
        except Exception as error:
            raise AppError("本地重排模型加载失败：" + str(error)[:200], 500) from None
    return _LOCAL_MODELS[path]

# 运行配置：区分demo/live、独立数据目录、聊天模型与Embedding模型。
@dataclass
class Config:
    data: Path = ROOT / "runtime"
    mode: str = "demo"
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v4"
    dimensions: int = 1024
    # 本地模型目录：默认留空（可移植），设置环境变量后才启用；
    # 未设置或目录不可用时自动退回 API（live）或哈希向量（demo）。
    embedding_path: str = os.getenv("EMBEDDING_MODEL_PATH", "")
    reranker_path: str = os.getenv("RERANKER_MODEL_PATH", "")

    # 从环境变量构造配置；仅校验取值，不把密钥打印到日志。
    @classmethod
    def env(cls):
        mode = os.getenv("APP_MODE", "demo")
        if mode not in {"demo", "live"}:
            raise AppError("APP_MODE只能为demo或live")
        return cls(Path(os.getenv("APP_DATA", str(ROOT / "runtime"))), mode,
                   os.getenv("DASHSCOPE_API_KEY", ""),
                   os.getenv("MODEL_BASE_URL", cls.base_url),
                   os.getenv("CHAT_MODEL", "qwen-plus"),
                   embedding_path=os.getenv("EMBEDDING_MODEL_PATH", cls.embedding_path),
                   reranker_path=os.getenv("RERANKER_MODEL_PATH", cls.reranker_path))

# SQLite持久化入口：建立本项目表结构，并集中管理事务和连接关闭。
class Store:
    # 保存配置并建立表结构；实例可重开已有数据库，不会清空原记录。
    def __init__(self, config):
        self.config = config
        # 按需建立目录，已存在时按给定选项处理
        config.data.mkdir(parents=True, exist_ok=True)
        self.path = config.data / "app.db"
        with self.db() as db:
            # 创建缺失表结构；IF NOT EXISTS不会清空已有数据
            db.executescript("""
            CREATE TABLE IF NOT EXISTS documents(
              id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,sha TEXT NOT NULL,
              version INTEGER NOT NULL,backend TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(
              id TEXT PRIMARY KEY,doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
              page INTEGER,section TEXT,text TEXT NOT NULL,vector TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
              id TEXT PRIMARY KEY,messages TEXT NOT NULL DEFAULT '[]',busy_until REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),
              request_id TEXT NOT NULL,payload_hash TEXT NOT NULL,version TEXT NOT NULL,
              order_id TEXT NOT NULL,issue TEXT NOT NULL,state TEXT NOT NULL,
              ticket_id TEXT,created REAL NOT NULL,UNIQUE(session_id,request_id));
            CREATE TABLE IF NOT EXISTS tickets(
              id TEXT PRIMARY KEY,task_id TEXT UNIQUE NOT NULL REFERENCES tasks(id),
              order_id TEXT NOT NULL,issue TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS traces(
              id TEXT PRIMARY KEY,project TEXT NOT NULL,session_id TEXT,created REAL NOT NULL,
              status TEXT NOT NULL,elapsed_ms REAL NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback(
              id TEXT PRIMARY KEY,trace_id TEXT NOT NULL REFERENCES traces(id),rating INTEGER NOT NULL,
              comment TEXT NOT NULL,created REAL NOT NULL);
            """)

    # 上下文管理器：正常退出提交，异常回滚，最后显式关闭连接以避免Windows文件占用。
    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        # WAL允许读写并发，busy_timeout避免瞬时写锁直接报错（并发建单更稳）。
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=NORMAL")
        db.row_factory = sqlite3.Row
        # 每个连接都启用外键，documents删除后关联chunks才会按约束级联清理。
        # 启用当前SQLite连接的约束/运行设置
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            # 提交当前连接尚未提交的修改，使其持久保存
            db.commit()
        except BaseException:
            # 撤回本次事务未完成的修改，避免半成品数据
            db.rollback()
            raise
        finally:
            # 关闭资源，释放连接或文件占用
            db.close()

    # 为一次操作分配唯一记录ID，保存状态、耗时和payload；这是排错证据，不是模型准确率。
    def trace(self, project, session, status, start, payload):
        trace_id = uuid.uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO traces VALUES(?,?,?,?,?,?,?)", (
                trace_id, project, session, time.time(), status,
                round((time.perf_counter() - start) * 1000, 2), json.dumps(payload, ensure_ascii=False)))
        return trace_id

# 可选依赖：装了jieba就把中文切成词，没装则退回单字+双字，行为与旧版一致。
try:
    import jieba as _jieba

    _jieba.setLogLevel(60)
except Exception:
    _jieba = None

# 给本地检索构造词项：英文数字串小写化；中文有jieba按词切，没有则单字+双字。
def tokens(text):
    text = text.lower()
    words = re.findall(r"[a-z0-9_]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        # 有分词器时按词切分，"向量数据库"不会被拆成四个碎片
        if _jieba is not None:
            words.extend(word for word in _jieba.cut(run) if word.strip())
            continue
        # 没有分词器时保持旧行为：单字 + 连续双字
        words.extend(run)
        words.extend(run[i:i + 2] for i in range(len(run) - 1))
    return words

# 外部模型适配器：业务只调用embed/chat等方法，网络请求细节集中在此。
class Provider:
    # 每次业务操作使用独立事件列表，便于把该次接口用量归到对应trace。
    def __init__(self, config):
        self.config = config
        self.events = []

    # 单次POST封装：仅对超时/连接错误/5xx重试一次，4xx直接失败（参数或密钥问题重试无意义）。
    def _post(self, path, payload):
        url = self.config.base_url.rstrip("/") + "/" + path
        headers = {"Authorization": "Bearer " + self.config.api_key}
        last = None
        for attempt in range(2):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=(10, 90))
            except (requests.Timeout, requests.ConnectionError) as error:
                last = error
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise
            if response.status_code >= 500 and attempt == 0:
                last = AppError("模型接口失败，HTTP " + str(response.status_code), 502)
                time.sleep(0.5)
                continue
            if response.status_code >= 400:
                raise AppError("模型接口失败，HTTP " + str(response.status_code), 502)
            return response
        raise last if isinstance(last, Exception) else AppError("模型接口失败", 502)

    # 发起一次模型HTTP调用并记录耗时/usage；瞬时故障重试一次，仍失败则上报。
    def request(self, path, payload):
        if not self.config.api_key:
            raise AppError("live模式未配置DASHSCOPE_API_KEY", 503)
        # 保存单调计时起点；后续用时间差计算本次操作耗时，不受系统时钟校准影响。
        start = time.perf_counter()
        event = {"operation": path, "model": payload.get("model"), "status": "failed"}
        try:
            response = self._post(path, payload)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("invalid response")
            event.update(status="completed", usage=result.get("usage"))
            if result.get("choices"):
                event["response"] = result["choices"][0].get("message")
            return result
        except requests.RequestException:
            raise AppError("模型连接失败或超时；已重试1次仍失败", 502) from None
        except (ValueError, KeyError, TypeError):
            raise AppError("模型返回结构无效", 502) from None
        finally:
            event["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
            self.events.append(event)

    # 将输入文本列表按顺序变成向量；本地模型优先，其次哈希替身（demo）或 API（live）。
    def embed(self, texts):
        info = local_embedding_info(self.config.embedding_path)
        if info:
            model = _load_sentence_transformer(self.config.embedding_path)
            vectors = model.encode(list(texts), normalize_embeddings=True, batch_size=16,
                                   show_progress_bar=False)
            batch = [vector.tolist() for vector in vectors]
            if any(len(vector) != info[1] for vector in batch):
                raise AppError("本地Embedding维度与配置不一致", 500)
            self.events.append({"operation": "embeddings", "mode": "local", "model": info[0], "usage": None})
            return batch
        if self.config.mode == "demo":
            vectors = []
            for text in texts:
                vec = np.zeros(self.config.dimensions)
                for word in tokens(text):
                    digest = hashlib.sha256(word.encode()).digest()
                    vec[int.from_bytes(digest[:4], "big") % len(vec)] += 1
                # 将向量除以自身长度做归一化；零向量以1作分母防止除零，仍保持零向量。
                vec /= np.linalg.norm(vec) or 1
                vectors.append(vec.tolist())
            self.events.append({"operation": "embeddings", "mode": "demo_hash", "usage": None})
            return vectors
        values = []
        # 按输入顺序分批向量化，返回index排序后再拼接，保持片段与向量一一对应。
        for offset in range(0, len(texts), 10):
            result = self.request("embeddings", {"model": self.config.embedding_model,
                "input": texts[offset:offset + 10], "dimensions": self.config.dimensions})
            try:
                rows = sorted(result["data"], key=lambda item: item["index"])
                batch = [row["embedding"] for row in rows]
                if len(batch) != len(texts[offset:offset + 10]):
                    raise ValueError()
                for vector in batch:
                    if len(vector) != self.config.dimensions or not np.isfinite(vector).all():
                        raise ValueError()
                values.extend(batch)
            except (KeyError, TypeError, ValueError):
                raise AppError("Embedding返回数量或维度无效", 502) from None
        return values

    def embed_query(self, question):
        """检索用的查询向量：本地 bge 要加指令前缀，文档侧不加。"""
        if local_embedding_info(self.config.embedding_path):
            return self.embed([QUERY_PREFIX + question])[0]
        return self.embed([question])[0]

    def rank(self, question, texts):
        """本地交叉编码器重排；返回分数列表，模型不可用时返回 None 交给调用方兜底。"""
        path = self.config.reranker_path
        if not path or not _local_st_available() or not (Path(path) / "config.json").exists():
            return None
        model = _load_cross_encoder(path)
        scores = model.predict([(question, text) for text in texts])
        return [float(score) for score in scores]

    # 一次性聊天请求，可附工具说明或JSON输出约束；响应仍需业务层继续检查。
    def chat(self, messages, tools=None, tool_choice="auto", json_mode=False):
        payload = {"model": self.config.model, "messages": messages, "stream": False,
                   "max_tokens": 1600, "temperature": 0}
        if tools:
            payload.update(tools=tools, tool_choice=tool_choice)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        result = self.request("chat/completions", payload)
        try:
            message = result["choices"][0]["message"]
            if message.get("role") != "assistant":
                raise ValueError()
            return message
        except (KeyError, TypeError, IndexError, ValueError):
            raise AppError("回答消息结构无效", 502) from None

# 约束外部文本非空且不过长，返回去除首尾空白后的字符串。
def required_text(text, limit=4000):
    if not isinstance(text, str) or not text.strip() or len(text) > limit:
        raise AppError("请输入非空文本，长度不超过" + str(limit))
    return text.strip()

# 模型结构化输出仍需解析和校验；只接受JSON对象，不执行模型生成的代码。
def parse_json(text):
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise AppError("模型未返回有效JSON对象；本次停止", 502) from None
