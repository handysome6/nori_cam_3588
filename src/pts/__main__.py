from .pts_controller import main
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='云台控制程序')
    parser.add_argument('--port', type=str, default="COM4", help='串口端口号 (默认: COM4)')
    
    args = parser.parse_args()
    main(port=args.port)
