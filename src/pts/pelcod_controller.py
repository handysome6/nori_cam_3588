import serial
import time
from loguru import logger


class PelcoDController:
    def __init__(self, port='COM4', address=0x01):
        self.serial = serial.Serial(
            port=port,
            baudrate=2400,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=1,
            timeout=1,
        )
        self.address = address

    def _calculate_checksum(self, command):
        return sum(command[1:6]) % 256

    def _send_command(self, command):
        """发送命令"""
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()

        # 发送新命令
        checksum = self._calculate_checksum(command)
        command.append(checksum)
        self.serial.write(bytes(command))
        time.sleep(0.1)  # 等待命令执行

    def _send_query_command(self, command):
        """发送查询命令并读取返回值"""
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()

        checksum = self._calculate_checksum(command)
        command.append(checksum)
        self.serial.write(bytes(command))
        time.sleep(0.1)  # 等待响应
        
        # read all data in a iterative way
        response=bytearray();prev=None
        while True:
            response+= self.serial.read(1)
            if prev == response:
                break
            prev=response.copy()
        response=bytes(response)

        # 读取7字节的响应
        if len(response) != 7:
            logger.error(f"未收到云台响应: {[hex(x) for x in response]}")
            raise TimeoutError("未收到云台响应")
        else:
            logger.debug(f"收到云台响应: {[hex(x) for x in response]}")
        
        # 返回位置值（byte5和byte6）
        return (response[4] << 8) | response[5]
    

        # other reading methods
        # response=self.serial.readline()
        # data_left = self.serial.in_waiting
        # response = self.serial.read(7)
