# shop_api（ChangeProof 受控种子仓库）

ChangeProof 评测语料的种子工程：一个可运行的 FastAPI + SQLAlchemy 小型电商服务。

- `app/core/`：高风险纯函数目录（金额、定价、校验），不依赖框架。
- `app/models.py`：`Customer` / `Product` / `Order` / `OrderItem` / `Coupon` 五个模型。
- `app/services/`：目录、订单、客户、优惠券、库存五个服务模块。
- `app/api/`：健康检查、商品、订单、客户四个路由文件。
- `tests/`：pytest 用例，全部不依赖数据库与网络。

基线版本全部测试通过；评测样本由 `scripts/bootstrap_samples.py` 从基线派生，
每个样本只注入一次显式声明的变更。