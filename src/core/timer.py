import time
import threading
import weakref
import calendar
from datetime import datetime
from typing import Callable, Optional

from PySide6.QtCore import QTimer

from src.util import Singleton, logger

# 负责计时、定时任务的模块

class TimerManager(Singleton):
    """全局计时器管理器 - 统一管理所有 QTimer"""

    def _init_impl(self):
        self._timers = []
        self._one_shot_timers = []
    
    def create_timer(self, parent=None) -> QTimer:
        """创建并注册计时器"""
        timer = QTimer(parent)
        self._timers.append(timer)
        return timer
    
    def create_one_shot(self, interval: int, callback: Callable, parent=None) -> QTimer:
        """创建单次触发的计时器"""
        timer = QTimer(parent)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: (callback(), self._remove_one_shot(timer)))
        timer.start(interval)
        self._one_shot_timers.append(timer)
        return timer
    
    def _remove_one_shot(self, timer: QTimer):
        """移除单次计时器"""
        if timer in self._one_shot_timers:
            self._one_shot_timers.remove(timer)
        if timer in self._timers:
            self._timers.remove(timer)
    
    def stop_all(self):
        """停止并清理所有计时器"""
        for timer in self._timers[:]:
            if timer.isActive():
                timer.stop()
            try:
                timer.deleteLater()
            except Exception:
                pass
        self._timers.clear()
        self._one_shot_timers.clear()


class WeakCallbackSet:
    """使用弱引用的回调集合，防止内存泄漏"""
    
    def __init__(self):
        self._callbacks = []
        self._weak_refs = []
    
    def add(self, callback: Callable) -> bool:
        """添加回调，返回是否添加成功"""
        try:
            wr = weakref.ref(callback, lambda ref: self._remove_dead(ref))
            if wr not in self._weak_refs:
                self._callbacks.append(callback)
                self._weak_refs.append(wr)
                return True
        except TypeError:
            pass
        return False
    
    def remove(self, callback: Callable):
        """移除回调"""
        try:
            wr = weakref.ref(callback)
            if wr in self._weak_refs:
                idx = self._weak_refs.index(wr)
                self._weak_refs.pop(idx)
                self._callbacks.pop(idx)
        except (ValueError, TypeError):
            pass
    
    def _remove_dead(self, ref):
        """移除已失效的弱引用"""
        try:
            if ref in self._weak_refs:
                idx = self._weak_refs.index(ref)
                self._weak_refs.pop(idx)
                if idx < len(self._callbacks):
                    self._callbacks.pop(idx)
        except (ValueError, IndexError):
            pass
    
    def __iter__(self):
        return iter(self._callbacks)
    
    def __len__(self):
        return len(self._callbacks)
    
    def __bool__(self):
        return bool(self._callbacks)


class LRUCache:
    """简单的 LRU 缓存"""
    
    def __init__(self, max_size: int = 10):
        self._cache = {}
        self._order = []
        self._max_size = max_size
    
    def get(self, key, default=None):
        return self._cache.get(key, default)
    
    def set(self, key, value):
        if key in self._cache:
            self._order.remove(key)
        elif len(self._cache) >= self._max_size:
            oldest = self._order.pop(0)
            self._cache.pop(oldest, None)
        self._cache[key] = value
        self._order.append(key)
    
    def clear(self):
        self._cache.clear()
        self._order.clear()
    
    def __contains__(self, key):
        return key in self._cache
    
    def __len__(self):
        return len(self._cache)


def single_shot(interval: int, callback: Callable, parent=None):
    """便捷函数：单次定时执行"""
    manager = TimerManager()
    return manager.create_one_shot(interval, callback, parent)


def delayed_call(interval: int, callback: Callable, parent=None) -> QTimer:
    """延迟执行回调（与 single_shot 等效）"""
    return single_shot(interval, callback, parent)


class _CronField:
    """解析后的单个 cron 字段，支持匹配和查找下一个匹配值"""

    def __init__(self, expr: str, min_val: int, max_val: int):
        self._values: set[int] = set()
        for part in expr.split(','):
            part = part.strip()
            if not part:
                continue
            if part == '*':
                self._values.update(range(min_val, max_val + 1))
            elif '*/' in part:
                step = int(part.split('*/')[1])
                self._values.update(range(min_val, max_val + 1, step))
            elif '-' in part:
                if '/' in part:
                    range_part, step = part.split('/')
                    rmin, rmax = range_part.split('-')
                    self._values.update(range(int(rmin), int(rmax) + 1, int(step)))
                else:
                    rmin, rmax = part.split('-')
                    self._values.update(range(int(rmin), int(rmax) + 1))
            else:
                v = int(part)
                if v < min_val or v > max_val:
                    raise ValueError(
                        f"Value {v} out of range [{min_val}, {max_val}] "
                        f"in cron field {expr!r}"
                    )
                self._values.add(v)
        if not self._values:
            raise ValueError(f"Empty field after parsing {expr!r}")
        self._is_all = (len(self._values) == max_val - min_val + 1)

    def match(self, v: int) -> bool:
        return v in self._values

    def next_match(self, v: int) -> Optional[int]:
        for candidate in sorted(self._values):
            if candidate >= v:
                return candidate
        return None


class _CronExpr:
    """解析后的 cron 表达式，可计算下一次匹配时间"""

    def __init__(self, expr: str):
        parts = expr.strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"Cron expression must have 5 fields (min hour dom month dow), "
                f"got {len(parts)}: {expr!r}"
            )
        self._minutes = _CronField(parts[0], 0, 59)
        self._hours = _CronField(parts[1], 0, 23)
        self._days_of_month = _CronField(parts[2], 1, 31)
        self._months = _CronField(parts[3], 1, 12)
        self._days_of_week = _CronField(parts[4], 0, 7)
        if 7 in self._days_of_week._values:
            self._days_of_week._values.add(0)

    def next_match(self, after: datetime) -> datetime:
        y, mo, d = after.year, after.month, after.day
        h, mi = after.hour, after.minute + 1

        dom_all = self._days_of_month._is_all
        dow_all = self._days_of_week._is_all

        while True:
            if mi >= 60:
                mi = 0; h += 1
            if h >= 24:
                h = 0; d += 1
            max_d = calendar.monthrange(y, mo)[1]
            if d > max_d:
                d = 1; mo += 1
            if mo > 12:
                mo = 1; y += 1

            nm = self._months.next_match(mo)
            if nm is None:
                y += 1; mo = 1; d = 1; h = 0; mi = 0
                continue
            if nm > mo:
                mo = nm; d = 1; h = 0; mi = 0
                continue

            max_d = calendar.monthrange(y, mo)[1]
            day_ok = False
            for try_d in range(d, max_d + 1):
                if dom_all and dow_all:
                    match = True
                elif dom_all:
                    match = self._days_of_week.match(_cron_weekday(y, mo, try_d))
                elif dow_all:
                    match = self._days_of_month.match(try_d)
                else:
                    match = (self._days_of_month.match(try_d)
                             or self._days_of_week.match(_cron_weekday(y, mo, try_d)))
                if match and try_d > d:
                    d = try_d; h = 0; mi = 0
                    day_ok = True
                    break
            if not day_ok:
                mo += 1; d = 1; h = 0; mi = 0
                continue

            nh = self._hours.next_match(h)
            if nh is None:
                d += 1; h = 0; mi = 0
                continue
            if nh > h:
                h = nh; mi = 0
                continue

            nm = self._minutes.next_match(mi)
            if nm is None:
                h += 1; mi = 0
                continue
            if nm > mi:
                mi = nm
                continue

            return datetime(y, mo, d, h, mi)


def _cron_weekday(year: int, month: int, day: int) -> int:
    """cron 星期约定：0=周日, 6=周六"""
    return (datetime(year, month, day).weekday() + 1) % 7


class CronTask:
    """基于 cron 表达式的定时任务调度器（后台线程，无需 Qt 事件循环）

    用法:
        task = CronTask(\"*/5 * * * *\", my_func)
        task.start()
        ...
        task.stop()

    支持标准 5 字段 cron 表达式 (分 时 日 月 周):
        \"*/5 * * * *\"   每 5 分钟
        \"0 * * * *\"     每小时整点
        \"0 0 * * *\"     每天零点
        \"0 9 * * 1-5\"   工作日早 9 点
    """

    def __init__(self, cron_expr: str, callback: Callable, *args,
                 daemon: bool = True, name: Optional[str] = None):
        self._expr = _CronExpr(cron_expr)
        self._callback = callback
        self._args = args
        self._daemon = daemon
        self._name = name or f"CronTask-{id(self)}"

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=self._daemon, name=self._name
            )
            self._thread.start()

    def stop(self):
        self.stop_nowait()
        thread = None
        with self._lock:
            thread = self._thread
        if thread and thread.is_alive() and not self._daemon:
            thread.join()

    def stop_nowait(self):
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            self._thread = None

    @property
    def running(self) -> bool:
        return self._running

    def is_running(self) -> bool:
        return self._running

    def _run_loop(self):
        while self._running:
            try:
                now = datetime.now()
                next_time = self._expr.next_match(now)
                delay = (next_time - datetime.now()).total_seconds()
                if delay < 0:
                    delay = 0

                if self._stop_event.wait(timeout=delay):
                    break

                if not self._running:
                    break

                try:
                    self._callback(*self._args)
                except Exception:
                    logger.exception("")

            except Exception:
                logger.exception("")
                if self._stop_event.wait(timeout=60):
                    break


def cron_schedule(cron_expr: str, callback: Callable, *args, **kwargs) -> CronTask:
    """创建并启动一个 cron 定时任务

    参数:
        cron_expr: cron 表达式（5 字段：分 时 日 月 周）
        callback: 要执行的函数
        *args: 传给 callback 的参数
        **kwargs: 传给 CronTask 的额外参数（daemon, name 等）

    返回:
        已启动的 CronTask 实例（可调用 stop() 停止）

    用法:
        task = cron_schedule(\"*/5 * * * *\", my_func)
        task.stop()
    """
    task = CronTask(cron_expr, callback, *args, **kwargs)
    task.start()
    return task