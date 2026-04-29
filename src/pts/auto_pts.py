from .pts_controller import PTSController
import numpy as np
import time
from loguru import logger

class PTSPositionGenerator:
    def __init__(self, center_pan=90, center_tilt=30, h_fov=60, v_fov=40, h_count=9, v_count=9):
        self.center_pan = center_pan
        self.center_tilt = center_tilt
        self.h_fov = h_fov
        self.v_fov = v_fov
        self.h_count = h_count
        self.v_count = v_count

    def update_params(self, center_pan=None, center_tilt=None, h_fov=None, v_fov=None, h_count=None, v_count=None):
        if center_pan is not None:
            self.center_pan = center_pan
        if center_tilt is not None:
            self.center_tilt = center_tilt
        if h_fov is not None:
            self.h_fov = h_fov
        if v_fov is not None:
            self.v_fov = v_fov
        if h_count is not None:
            self.h_count = h_count
        if v_count is not None:
            self.v_count = v_count

        logger.info(f"参数更新: {self.center_pan}, {self.center_tilt}, {self.h_fov}, {self.v_fov}, {self.h_count}, {self.v_count}")

    def top_left(self):
        return [self.center_pan - self.h_fov/2, self.center_tilt - self.v_fov/2]

    def top_right(self):
        return [self.center_pan + self.h_fov/2, self.center_tilt - self.v_fov/2]

    def bottom_left(self):
        return [self.center_pan - self.h_fov/2, self.center_tilt + self.v_fov/2]
    
    def bottom_right(self):
        return [self.center_pan + self.h_fov/2, self.center_tilt + self.v_fov/2]

    def generate_grid_positions(self):
        """
        在给定视场角范围内生成均匀分布的云台位置
        
        返回:
            positions: 包含[pan, tilt]坐标的列表
        """
        # 计算网格大小
        grid_h = self.h_count 
        grid_v = self.v_count
        
        # 计算位置范围
        pan_min = self.center_pan - self.h_fov/2
        pan_max = self.center_pan + self.h_fov/2
        tilt_min = self.center_tilt - self.v_fov/2
        tilt_max = self.center_tilt + self.v_fov/2
        
        # 生成网格点
        pan_positions = np.linspace(pan_min, pan_max, grid_h)
        tilt_positions = np.linspace(tilt_min, tilt_max, grid_v)
        
        # 创建位置列表
        positions = []
        for tilt in tilt_positions:
            for pan in pan_positions:
                # 确保pan角度在0-359范围内
                pan = pan % 360
                # 确保tilt角度在0-180范围内
                tilt = np.clip(tilt, 0, 180)
                positions.append([pan, tilt])
                
        return positions


def scan_positions(h_fov: float = 40, v_fov: float = 40, h_count: int = 9, v_count: int = 9, port: str="COM4"):
    """
    生成器函数，用于控制云台按网格扫描位置
    
    参数:
        h_fov: 水平视场角（度）
        v_fov: 垂直视场角（度）
        h_count: 水平网格点数
        v_count: 垂直网格点数
        port: 串口端口
        
    yields:
        dict: 包含当前位置信息的字典
            - target: [pan, tilt] 目标位置
            - actual: [pan, tilt] 实际位置
            - success: bool 是否成功到达位置
            - index: int 当前位置索引
    """
    try:
        controller = PTSController(port=port)
        logger.info("===== 云台自动控制系统 =====")
        
        # 生成位置列表
        position_generator = PTSPositionGenerator(
            center_pan=90,
            center_tilt=30,
            h_fov=h_fov,
            v_fov=v_fov,
            h_count=h_count,
            v_count=v_count
        )
        positions = position_generator.generate_grid_positions()
        
        logger.info(f"已生成 {len(positions)} 个位置点")
        logger.info("开始移动云台...")
        
        # 遍历所有位置
        for i, (pan, tilt) in enumerate(positions, 1):
            logger.info(f"移动到位置 {i}/{len(positions)}: Pan={pan:.1f}°, Tilt={tilt:.1f}°")
            
            result = {
                'target': [pan, tilt],
                'actual': None,
                'success': False,
                'index': i
            }
            
            # 设置云台位置并等待到达目标位置
            ret = controller.goto_position_blocked(pan, tilt)
            time.sleep(0.5)

            if ret:
                actual_pan, actual_tilt = controller.get_current_pose()
                if actual_pan is not None and actual_tilt is not None:
                    result['actual'] = [actual_pan, actual_tilt]
                    result['success'] = True
                    logger.info(f"实际位置: Pan={actual_pan:.1f}°, Tilt={actual_tilt:.1f}°")
                else:
                    logger.warning(f"警告：无法获取实际位置")
            else:
                logger.error("未能在规定时间内到达目标位置")
            
            yield result
    
    except Exception as e:
        logger.error(f"发生错误: {e}")
        logger.exception("详细错误信息：")
    finally:
        logger.info("程序已结束")

def main():
    try:
        count = int(input("请输入期望的位置点数量: "))
        
        # 使用生成器进行扫描
        for position_info in scan_positions(count):
            if position_info['success']:
                logger.info(f"成功到达位置 {position_info['index']}")
            else:
                logger.warning(f"位置 {position_info['index']} 移动失败")
            
            # 如果需要在每个位置暂停，可以取消下面这行的注释
            # input("按回车键继续移动到下一个位置...")
            
    except KeyboardInterrupt:
        logger.warning("程序被用户中断")
    except Exception as e:
        logger.error(f"发生错误: {e}")
        logger.exception("详细错误信息：")

if __name__ == "__main__":
    main()

    # test = PTSPositionGenerator(
    #     center_pan=90,
    #     center_tilt=30,
    #     h_fov=60,
    #     v_fov=40,
    #     h_count=2,
    #     v_count=4)
    # print(test.generate_grid_positions())
    # print(test.top_left())
    # print(test.top_right())
    # print(test.bottom_left())
    # print(test.bottom_right())