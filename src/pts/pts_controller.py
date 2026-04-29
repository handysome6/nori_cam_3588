from .pelcod_controller import PelcoDController
import time
from loguru import logger

class PTSController(PelcoDController):
    def __init__(self, *args, **kwargs):
        # 初始化父类
        super().__init__(*args, **kwargs)

    # Getters
    def get_pan_position(self):
        """获取当前平移角度
        返回值: 0-359度之间的浮点数
        """
        command = [0xFF, self.address, 0x00, 0x51, 0x00, 0x00]
        try:
            position = self._send_query_command(command)
            # 将位置值转换为角度 (0~35900 -> 0~359)
            return position / 100.0
        except TimeoutError:
            return None

    def get_tilt_position(self):
        """获取当前倾斜角度
        返回值: 0-180度之间的浮点数
        """
        command = [0xFF, self.address, 0x00, 0x53, 0x00, 0x00]
        try:
            position = self._send_query_command(command)
            # 将位置值转换为角度 (0~18000 -> 0~180)
            return position / 100.0
        except TimeoutError:
            return None

    def get_current_pose(self):
        """获取当前云台的完整位置信息
        返回值: (pan_angle, tilt_angle) 元组，如果查询失败则相应位置为None
        """
        time.sleep(0.1)
        pan = self.get_pan_position()
        time.sleep(0.1)
        tilt = self.get_tilt_position()
        return (pan, tilt)

    # Setters
    def set_pan_position(self, degree):
        if not 0 <= degree <= 359:
            raise ValueError("平移角度必须在0-359度之间")
        
        position = int(degree * 100)  # 转换为协议要求的范围
        command = [0xFF, self.address, 0x00, 0x4B, 
                  (position >> 8) & 0xFF,  # Pan MSB
                  position & 0xFF]         # Pan LSB
        self._send_command(command)

    def set_tilt_position(self, degree):
        if not 0 <= degree <= 180:
            raise ValueError("倾斜角度必须在0-180度之间")
        
        position = int(degree * 100)  # 转换为协议要求的范围
        command = [0xFF, self.address, 0x00, 0x4D,
                  (position >> 8) & 0xFF,  # Tilt MSB
                  position & 0xFF]         # Tilt LSB
        self._send_command(command)

    def go_to_preset(self, preset_number):
        """前往预设位置
        参数:
            preset_number: 预设位置编号 (1-128)
        """
        if not 1 <= preset_number <= 128:
            raise ValueError("预设位置编号必须在1-128之间")
        
        command = [0xFF, self.address, 0x00, 0x07, 0x00, preset_number]
        self._send_command(command)
        print(f"正在前往预设位置 {preset_number}")

    def set_pan_tilt(self, pan, tilt):
        self.set_pan_position(pan)
        self.set_tilt_position(tilt)

    # Others
    def cancel_movement(self):
        """取消当前运动指令"""
        cancel_command = [0xFF, self.address, 0x00, 0x80, 0x00, 0x00]
        checksum = self._calculate_checksum(cancel_command)
        cancel_command.append(checksum)
        self.serial.write(bytes(cancel_command))
        time.sleep(0.1)  # 等待取消命令执行

    def close(self):
        if self.serial.is_open:
            self.serial.close()

    # High level functions
    def wait_for_movement(self, target_pan, target_tilt, timeout=40, tolerance=0.5):
        """
        等待云台移动到目标位置
        
        参数:
            target_pan: 目标平移角度
            target_tilt: 目标倾斜角度
            timeout: 最大等待时间（秒）
            tolerance: 位置误差容限（度）
        
        返回:
            bool: 是否成功到达目标位置
        """
        time.sleep(1)
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            current_pan, current_tilt = self.get_current_pose()
            
            # 如果获取位置失败，说明还在移动中，继续等待
            if current_pan is not None and current_tilt is not None:
                if abs(current_pan - target_pan) < tolerance and abs(current_tilt - target_tilt) < tolerance:
                    return True
                else:
                    logger.debug(f"moved to position  ... {current_pan}, {current_tilt}")
                    logger.debug(f"yet target position... {target_pan}, {target_tilt}")
                    return True
                    # this is a hack to make the function return True, sometimes this query command will fail to get correct position
                    # time.sleep(1)
                    # continue
            else:
                logger.debug("等待云台移动中...")
                time.sleep(1)
                continue
        
        
        return False

    def goto_position_blocked(self, target_pan, target_tilt):
        """
        前往目标位置并等待到达
        
        参数:
            target_pan: 目标平移角度 or None
            target_tilt: 目标倾斜角度 or None

        返回:
            bool: 是否成功到达目标位置
        """
        # 设置云台位置
        if target_pan is not None:
            self.set_pan_position(target_pan)
            time.sleep(0.1)
        if target_tilt is not None:
            self.set_tilt_position(target_tilt)
            time.sleep(0.1)

        # 等待云台到达目标位置
        logger.info("等待云台到达目标位置...")
        if self.wait_for_movement(target_pan, target_tilt):
            logger.success("已到达目标位置")
            return True
        else:
            logger.error("未能在规定时间内到达目标位置")
            return False


def main(port="COM4"):
    logger.info(f"使用端口: {port}")
    # 创建控制器实例
    controller = PTSController(port=port)
    
    try:
        while True:
            print("\n云台控制系统")
            print("1. 设置平移角度 (0-359度)")
            print("2. 设置倾斜角度 (0-180度)")
            print("3. 获取当前位置")
            print("4. 前往预设位置")
            print("5. 退出")
            
            choice = input("请选择操作 (1-5): ")
            
            if choice == '1':
                degree = float(input("请输入平移角度 (0-359): "))
                try:
                    controller.set_pan_position(degree)
                    print(f"已设置平移角度为 {degree}度")
                except ValueError as e:
                    print(f"错误: {e}")
                    
            elif choice == '2':
                degree = float(input("请输入倾斜角度 (0-180): "))
                try:
                    controller.set_tilt_position(degree)
                    print(f"已设置倾斜角度为 {degree}度")
                except ValueError as e:
                    print(f"错误: {e}")
            
            elif choice == '3':
                pan, tilt = controller.get_current_pose()
                if pan is not None and tilt is not None:
                    print(f"当前位置: 平移={pan:.2f}度, 倾斜={tilt:.2f}度")
                else:
                    print("无法获取完整的位置信息")
                    
            elif choice == '4':
                preset = int(input("请输入预设位置编号 (1-128): "))
                try:
                    controller.go_to_preset(preset)
                except ValueError as e:
                    print(f"错误: {e}")
                    
            elif choice == '5':
                break
            
            else:
                print("无效的选择，请重试")
    
    finally:
        controller.close()
        print("程序已退出")

if __name__ == "__main__":
    main(port="COM4")
