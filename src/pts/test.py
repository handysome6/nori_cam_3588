import serial
import time

def calculate_checksum(command):
    # return sum(command[1:-1]) % 256
    return sum(command[1:]) % 256

def query_position(serial_port: serial.Serial):
    """
    查询云台当前的 Pan/Tilt/Zoom 位置
    返回: (pan, tilt, zoom) 元组
    """
    # 构建查询命令
    prefix_bytes = [0xFF, 0x01]  # 固定头部和地址
    query_bytes = [0x00, 0x93]   # 位置查询命令
    other_bytes = [0x00] * 6     # 补充6个字节的0
    
    command = prefix_bytes + query_bytes + other_bytes
    checksum = calculate_checksum(command)
    command.append(checksum)
    
    print(f"发送命令: {[hex(x) for x in command]}")
    
    # 发送命令
    serial_port.write(bytes(command))
    time.sleep(0.1)  # 等待响应
    
    # 读取11字节的响应
    response = serial_port.readline()

    print(f"原始响应: {[hex(x) for x in response]}")

    if len(response) != 11:
        raise TimeoutError("未收到完整的云台响应")
        
    print(f"原始响应: {[hex(x) for x in response]}")
        
    # 验证响应的校验和
    if response[0] != 0x00 or response[1] != 0x9B:
        raise ValueError("无效的响应格式")
    
    # 解析位置数据
    pan_position = (response[2] << 8) | response[3]    # Pan MSB和LSB
    tilt_position = (response[4] << 8) | response[5]   # Tilt MSB和LSB
    zoom_position = (response[6] << 8) | response[7]   # Zoom MSB和LSB
    
    return pan_position, tilt_position, zoom_position


def send_command(serial_port: serial.Serial, pan_degree, tilt_degree):
    """
    发送云台控制命令
    
    """
    position = int(pan_degree * 100)  # 转换为协议要求的范围
    pan_msb = (position >> 8) & 0xFF
    pan_lsb = position & 0xFF

    position = int(tilt_degree * 100)  # 转换为协议要求的范围
    tilt_msb = (position >> 8) & 0xFF
    tilt_lsb = position & 0xFF

    speed = (10*100)//2
    speed_msb = (speed >> 8) & 0xFF
    speed_lsb = speed & 0xFF

    # 构建查询命令
    prefix_bytes = [0xFF, 0x01]  # 固定头部和地址
    command_bytes = [0x00, 0x91]   # 位置查询命令

    position_bytes = [pan_msb, pan_lsb, tilt_msb, tilt_lsb, 0x00, 0x00, speed_msb, speed_lsb]
    checksum = calculate_checksum(prefix_bytes + command_bytes + position_bytes)
    command = prefix_bytes + command_bytes + position_bytes + [checksum]

    print(f"发送命令: {[hex(x) for x in command]}")

    serial_port.write(bytes(command))
    time.sleep(0.1)  # 等待响应



if __name__ == "__main__":
    # 初始化串口
    # serial_port = serial.Serial(
    #     port='COM4',
    #     baudrate=2400,
    #     bytesize=8,
    #     parity=serial.PARITY_NONE,
    #     stopbits=1,
    #     timeout=1
    # )
    serial_port = None
    
    try:
        print("开始发送命令...")
        send_command(serial_port, 100, 30)
        time.sleep(1)

        print("开始查询云台位置...")
        pan, tilt, zoom = query_position(serial_port)
        print(f"当前位置: Pan={pan}, Tilt={tilt}, Zoom={zoom}")
    except Exception as e:
        print(f"查询失败: {str(e)}")
    finally:
        serial_port.close()
        print("串口已关闭")


