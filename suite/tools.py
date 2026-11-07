"""模拟与 HTTP 查询工具适配，统一服务和订单查询的返回契约。"""

import time


class MockTools:
    """本地模拟实现：行为与原来写死的字典完全一致。"""

    def __init__(self, services, orders):
        self.services = services
        self.orders = orders

    def query_service(self, service_id):
        item = self.services.get(service_id)
        return {"found": item is not None, "service": item, "data_type": "自编模拟服务"}

    def query_order(self, order_id):
        item = self.orders.get(order_id)
        return {"found": item is not None, "order": item, "data_type": "自编模拟订单"}


class HttpTools:
    """真实系统占位实现：接公司内部 API 时填这里。

    约定：
    - 超时必须显式设置（连接 3s / 读取 10s），不能让请求无限挂起；
    - 4xx 视为业务不存在（found=False），5xx 抛异常由上层记录 trace；
    - 鉴权信息从环境变量读取，不写进代码。
    """

    def __init__(self, base_url, token=None, timeout=(3, 10)):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _get(self, path, params):
        import requests

        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        last = None
        # 上游抖动常见于 5xx 与超时，重试一次即可；4xx 是业务问题，不重试
        for attempt in range(2):
            try:
                response = requests.get(self.base_url + path, params=params, headers=headers, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as error:
                last = error
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise RuntimeError("上游连接失败") from None
            if response.status_code >= 500:
                last = RuntimeError("上游服务错误 HTTP " + str(response.status_code))
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise last
            if response.status_code >= 400:
                return None
            return response.json()
        raise last if isinstance(last, Exception) else RuntimeError("上游请求失败")

    def query_service(self, service_id):
        data = self._get("/services/" + service_id, {})
        return {"found": data is not None, "service": data, "data_type": "真实服务"}

    def query_order(self, order_id):
        data = self._get("/orders/" + order_id, {})
        return {"found": data is not None, "order": data, "data_type": "真实订单"}
