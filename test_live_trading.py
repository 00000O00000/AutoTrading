"""
工具实盘验证测试脚本

使用小额真实订单验证所有交易工具的有效性。
测试交易对: DOGE/USDT (低价格，便于小额测试)
最小测试金额: 12 USDT

警告: 此脚本会执行真实交易订单！
"""

import os
import sys
import time
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.bot.binance_client import BinanceClient
from app.bot.executor import TradeExecutor


class LiveTradingTest:
    """实盘交易工具测试"""
    
    # 测试配置
    SYMBOL = "DOGE/USDT"
    TEST_AMOUNT_USDT = 12.0  # 最小测试金额
    TEST_LEVERAGE = 5
    
    def __init__(self):
        # 获取 API 密钥
        api_key = os.getenv('BINANCE_API_KEY', '')
        api_secret = os.getenv('BINANCE_API_SECRET', '')
        
        if not api_key or not api_secret:
            raise ValueError("请在 .env 文件中配置 BINANCE_API_KEY 和 BINANCE_API_SECRET")
        
        # 初始化客户端
        self.client = BinanceClient(api_key, api_secret)
        self.executor = TradeExecutor(self.client)
        
        # 测试结果
        self.results = []
    
    def log(self, message: str, success: bool = None):
        """打印测试日志"""
        if success is True:
            prefix = "[PASS]"
        elif success is False:
            prefix = "[FAIL]"
        else:
            prefix = "[INFO]"
        print(f"{prefix} {message}")
    
    def add_result(self, test_name: str, success: bool, details: str = ""):
        """记录测试结果"""
        self.results.append({
            "name": test_name,
            "success": success,
            "details": details
        })
        self.log(f"{test_name}: {details}", success)
    
    def wait(self, seconds: float = 1.0, reason: str = ""):
        """等待一段时间"""
        if reason:
            print(f"  ... 等待 {seconds}s ({reason})")
        time.sleep(seconds)
    
    # ==========================================================================
    # 测试用例
    # ==========================================================================
    
    def test_1_set_leverage(self):
        """测试 1: 设置杠杆"""
        print("\n" + "="*50)
        print("测试 1: set_leverage")
        print("="*50)
        
        result = self.executor.set_leverage(self.SYMBOL, self.TEST_LEVERAGE)
        
        if result.success:
            self.add_result("set_leverage", True, f"杠杆设置为 {self.TEST_LEVERAGE}x")
        else:
            self.add_result("set_leverage", False, f"错误: {result.error}")
        
        return result.success
    
    def test_2_set_margin_mode(self):
        """测试 2: 设置保证金模式"""
        print("\n" + "="*50)
        print("测试 2: set_margin_mode")
        print("="*50)
        
        result = self.executor.set_margin_mode(self.SYMBOL, "isolated")
        
        if result.success:
            self.add_result("set_margin_mode", True, "设置为逐仓模式")
        else:
            # 如果已经是该模式，也算成功
            if "already" in str(result.error).lower() or "no need" in str(result.error).lower():
                self.add_result("set_margin_mode", True, "已经是逐仓模式")
                return True
            self.add_result("set_margin_mode", False, f"错误: {result.error}")
        
        return result.success
    
    def test_3_open_position_with_sl_tp(self):
        """测试 3: 开仓（带止损止盈）"""
        print("\n" + "="*50)
        print("测试 3: trade_in (open_position with SL/TP)")
        print("="*50)
        
        # 获取当前价格
        ticker = self.client.fetch_ticker(self.SYMBOL)
        current_price = ticker.last_price
        print(f"  当前价格: ${current_price:.4f}")
        
        # 设置止损止盈价格 (5% 止损, 10% 止盈)
        stop_loss_price = round(current_price * 0.95, 5)
        take_profit_price = round(current_price * 1.10, 5)
        
        print(f"  止损价格: ${stop_loss_price:.5f} (-5%)")
        print(f"  止盈价格: ${take_profit_price:.5f} (+10%)")
        
        result = self.executor.open_position(
            symbol=self.SYMBOL,
            side="BUY",
            amount_usdt=self.TEST_AMOUNT_USDT,
            leverage=self.TEST_LEVERAGE,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price
        )
        
        if result.success:
            details = f"开仓成功 @ ${result.executed_price:.5f}, 数量={result.quantity}"
            if result.sl_order_id:
                details += f", SL_ID={result.sl_order_id}"
            if result.tp_order_id:
                details += f", TP_ID={result.tp_order_id}"
            self.add_result("open_position", True, details)
            
            # 保存订单 ID 供后续测试使用
            self.sl_order_id = result.sl_order_id
            self.tp_order_id = result.tp_order_id
        else:
            self.add_result("open_position", False, f"错误: {result.error}")
        
        return result.success
    
    def test_4_get_pending_orders(self):
        """测试 4: 获取挂单列表"""
        print("\n" + "="*50)
        print("测试 4: get_open_orders (验证挂单列表)")
        print("="*50)
        
        self.wait(1, "等待订单同步")
        
        orders = self.client.get_open_orders(self.SYMBOL)
        
        if orders:
            print(f"  找到 {len(orders)} 个挂单:")
            for order in orders:
                print(f"    - ID: {order['id']}, 类型: {order.get('type')}, "
                      f"方向: {order.get('side')}, 触发价: {order.get('stopPrice')}, "
                      f"is_algo: {order.get('is_algo', False)}")
            self.add_result("get_open_orders", True, f"找到 {len(orders)} 个挂单")
            return True
        else:
            self.add_result("get_open_orders", False, "未找到挂单")
            return False
    
    def test_5_modify_position(self):
        """测试 5: 修改止盈止损"""
        print("\n" + "="*50)
        print("测试 5: modify_position")
        print("="*50)
        
        # 获取当前价格
        ticker = self.client.fetch_ticker(self.SYMBOL)
        current_price = ticker.last_price
        
        # 新的止损止盈价格 (4% 止损, 8% 止盈)
        new_stop_loss_price = round(current_price * 0.96, 5)
        new_take_profit_price = round(current_price * 1.08, 5)
        
        print(f"  新止损价格: ${new_stop_loss_price:.5f} (-4%)")
        print(f"  新止盈价格: ${new_take_profit_price:.5f} (+8%)")
        
        result = self.executor.modify_position_tpsl(
            symbol=self.SYMBOL,
            stop_loss_price=new_stop_loss_price,
            take_profit_price=new_take_profit_price
        )
        
        if result.success:
            self.add_result("modify_position", True, "止盈止损已修改")
        else:
            self.add_result("modify_position", False, f"错误: {result.error}")
        
        return result.success
    
    def test_6_cancel_single_order(self):
        """测试 6: 按 ID 取消单个订单"""
        print("\n" + "="*50)
        print("测试 6: cancel_order (按 ID 取消)")
        print("="*50)
        
        self.wait(1, "等待订单同步")
        
        # 获取当前挂单
        orders = self.client.get_open_orders(self.SYMBOL)
        
        if not orders:
            self.add_result("cancel_order", False, "没有可取消的订单")
            return False
        
        # 取消第一个订单
        target_order = orders[0]
        order_id = target_order['id']
        print(f"  准备取消订单: ID={order_id}, 类型={target_order.get('type')}")
        
        result = self.executor.cancel_order_by_id(self.SYMBOL, order_id)
        
        if result.success:
            self.add_result("cancel_order", True, f"订单 {order_id} 已取消")
        else:
            self.add_result("cancel_order", False, f"错误: {result.error}")
        
        return result.success
    
    def test_7_cancel_orders_by_type(self):
        """测试 7: 按类型取消订单"""
        print("\n" + "="*50)
        print("测试 7: cancel_orders (按类型取消)")
        print("="*50)
        
        # 先重新设置一个止损单用于测试
        position = self.client.get_position_size(self.SYMBOL)
        if position and position['contracts'] > 0:
            ticker = self.client.fetch_ticker(self.SYMBOL)
            current_price = ticker.last_price
            stop_loss_price = round(current_price * 0.95, 5)
            
            try:
                opposite_side = 'SELL' if position['side'] == 'LONG' else 'BUY'
                self.client.create_stop_loss_order(
                    self.SYMBOL, opposite_side, position['contracts'], stop_loss_price
                )
                print(f"  已创建测试止损单 @ ${stop_loss_price:.5f}")
                self.wait(1, "等待订单创建")
            except Exception as e:
                print(f"  创建止损单失败: {e}")
        
        # 取消所有止损单
        result = self.executor.cancel_orders(self.SYMBOL, "stop_loss")
        
        if result.success:
            cancelled_count = int(result.quantity) if result.quantity else 0
            self.add_result("cancel_orders", True, f"取消了 {cancelled_count} 个止损单")
        else:
            self.add_result("cancel_orders", False, f"错误: {result.error}")
        
        return result.success
    
    def test_8_close_position(self):
        """测试 8: 平仓"""
        print("\n" + "="*50)
        print("测试 8: close_position")
        print("="*50)
        
        result = self.executor.close_position(
            symbol=self.SYMBOL,
            percentage=100,
            reason="测试完成，平仓退出"
        )
        
        if result.success:
            self.add_result("close_position", True, 
                           f"平仓成功 @ ${result.executed_price:.5f}, 数量={result.quantity}")
        else:
            # 如果没有仓位也算通过
            if "position" in str(result.error).lower():
                self.add_result("close_position", True, "没有仓位需要平仓")
                return True
            self.add_result("close_position", False, f"错误: {result.error}")
        
        return result.success
    
    def test_9_verify_cleanup(self):
        """测试 9: 验证清理（无残留订单）"""
        print("\n" + "="*50)
        print("测试 9: 验证清理")
        print("="*50)
        
        self.wait(2, "等待清理完成")
        
        # 检查是否有残留挂单
        orders = self.client.get_open_orders(self.SYMBOL)
        
        if orders:
            print(f"  警告: 发现 {len(orders)} 个残留挂单，尝试清理...")
            self.client.cancel_all_orders(self.SYMBOL)
            self.wait(1, "等待清理")
            orders = self.client.get_open_orders(self.SYMBOL)
        
        # 检查是否有残留仓位
        position = self.client.get_position_size(self.SYMBOL)
        has_position = position and position['contracts'] > 0
        
        if not orders and not has_position:
            self.add_result("cleanup_verification", True, "无残留订单和仓位")
            return True
        else:
            details = []
            if orders:
                details.append(f"{len(orders)} 个残留订单")
            if has_position:
                details.append(f"残留仓位 {position['contracts']}")
            self.add_result("cleanup_verification", False, ", ".join(details))
            return False
    
    # ==========================================================================
    # 运行测试
    # ==========================================================================
    
    def run_all_tests(self):
        """运行所有测试"""
        print("\n" + "="*60)
        print("  实盘交易工具验证测试")
        print(f"  交易对: {self.SYMBOL}")
        print(f"  测试金额: {self.TEST_AMOUNT_USDT} USDT")
        print("="*60)
        
        # 确认提示
        print("\n警告: 此测试将执行真实交易订单！")
        print("请确保您的账户有足够余额且理解相关风险。")
        confirm = input("\n输入 'yes' 继续: ")
        
        if confirm.lower() != 'yes':
            print("测试已取消")
            return
        
        print("\n开始测试...\n")
        
        # 运行测试
        try:
            self.test_1_set_leverage()
            self.wait(0.5)
            
            self.test_2_set_margin_mode()
            self.wait(0.5)
            
            if self.test_3_open_position_with_sl_tp():
                self.test_4_get_pending_orders()
                self.wait(0.5)
                
                self.test_5_modify_position()
                self.wait(0.5)
                
                self.test_6_cancel_single_order()
                self.wait(0.5)
                
                self.test_7_cancel_orders_by_type()
                self.wait(0.5)
                
                self.test_8_close_position()
                self.wait(0.5)
            
            self.test_9_verify_cleanup()
            
        except Exception as e:
            print(f"\n测试过程中发生错误: {e}")
            import traceback
            traceback.print_exc()
            
            # 紧急清理
            print("\n执行紧急清理...")
            try:
                self.client.cancel_all_orders(self.SYMBOL)
                position = self.client.get_position_size(self.SYMBOL)
                if position and position['contracts'] > 0:
                    close_side = 'SELL' if position['side'] == 'LONG' else 'BUY'
                    self.client.create_market_order(self.SYMBOL, close_side, position['contracts'])
                print("紧急清理完成")
            except Exception as cleanup_error:
                print(f"紧急清理失败: {cleanup_error}")
        
        # 打印测试报告
        self.print_report()
    
    def print_report(self):
        """打印测试报告"""
        print("\n" + "="*60)
        print("  测试报告")
        print("="*60)
        
        passed = sum(1 for r in self.results if r['success'])
        failed = sum(1 for r in self.results if not r['success'])
        total = len(self.results)
        
        for result in self.results:
            status = "PASS" if result['success'] else "FAIL"
            print(f"  [{status}] {result['name']}: {result['details']}")
        
        print("\n" + "-"*40)
        print(f"  通过: {passed}/{total}")
        print(f"  失败: {failed}/{total}")
        print("="*60)
        
        if failed == 0:
            print("\n所有测试通过！工具执行验证成功。")
        else:
            print("\n有测试未通过，请检查上述错误。")


if __name__ == "__main__":
    test = LiveTradingTest()
    test.run_all_tests()
