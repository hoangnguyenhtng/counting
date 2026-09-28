# Fold Tape Detection

Ứng dụng thị giác máy tính dùng để kiểm tra lỗi trong quy trình gắn vinyl/tape. Chương trình nhận trigger từ PLC hoặc thiết bị ngoại vi, chụp ảnh từ camera, chạy mô hình YOLO và trả kết quả `OK`/`NG`.

## Chức năng chính

- Kết nối camera HIK, SODA/Basler hoặc webcam.
- Điều khiển đèn qua cổng serial.
- Nhận trigger qua TCP hoặc COM.
- Phát hiện đối tượng bằng mô hình Ultralytics YOLO.
- Thiết lập vùng kiểm tra trực tiếp trên giao diện.
- Lưu kết quả kiểm tra vào SQLite và ảnh log theo ngày.
- Tự động quản lý dung lượng ảnh log.
- Chế độ `BYPASS`: vẫn chụp và lưu ảnh, bỏ qua model và trả kết quả `OK`.
- Đóng gói ứng dụng Windows bằng PyInstaller.

## Yêu cầu

- Windows 10/11.
- Python 3.11 được khuyến nghị.
- Driver/SDK của camera tương ứng nếu sử dụng camera công nghiệp.
- Thiết bị camera, bộ điều khiển đèn và PLC phù hợp với cấu hình triển khai.

## Cài đặt

Tạo môi trường ảo và cài thư viện:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Nếu PowerShell chặn script kích hoạt môi trường, có thể chạy trực tiếp:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Chạy chương trình

```powershell
python main.py
```

Cấu hình mặc định nằm tại:

```text
Settings/ModelSettings/Default/config.json
```

Các model AI nằm trong `res/ModelAI/`. Tên model được chọn trong file cấu hình phải tồn tại trong thư mục này.

## Chế độ BYPASS

`BYPASS` chỉ nên được sử dụng khi bảo trì hoặc kiểm tra thiết bị. Với mỗi trigger hợp lệ, hệ thống sẽ:

1. Bật đèn và chụp ảnh bình thường.
2. Lưu ảnh gốc vào `ImageCapture/YYYY_MM_DD/`.
3. Bỏ qua bước chạy model.
4. Trả kết quả `OK`.
5. Ghi kết quả `BYPASS_OK` và lưu ảnh vào thư mục Image Logs.

## Dữ liệu phát sinh khi chạy

Các mục sau được tạo tự động và không được đưa lên GitHub:

- `ImageCapture/`: ảnh gốc từ camera.
- `Log_Vision/`: log hệ thống và ảnh kết quả kiểm tra.
- `database.db`: cơ sở dữ liệu SQLite.
- `build/`, `dist/`: kết quả đóng gói PyInstaller.
- `.venv/`, `__pycache__/`, `.pytest_cache/`: môi trường và cache cục bộ.

## Chạy kiểm thử

```powershell
pip install pytest
python -m pytest tests -q
```

## Đóng gói ứng dụng

Cài bộ thư viện build đã được cố định phiên bản:

```powershell
pip install -r requirements-build.txt
```

Tạo bộ chạy Windows:

```powershell
python -m PyInstaller --clean --noconfirm main.spec
```

Kết quả được tạo tại `dist/VisionProtectVinylAttach/`.

## Cấu trúc dự án

```text
app/                 Giao diện, controller, worker và xử lý nghiệp vụ
data/                Ghi log, cấu hình JSON và cơ sở dữ liệu
hardware/            Camera, TCP/COM và bộ điều khiển đèn
res/                  Model AI, biểu tượng, stylesheet và tài nguyên UI
Settings/             Cấu hình model/phần cứng
tests/                Kiểm thử tự động
main.py               Điểm khởi chạy chương trình
main.spec             Cấu hình đóng gói PyInstaller
requirements.txt      Danh sách thư viện Python
```

## Bảo mật

Mật khẩu Teaching có thể được cấu hình khi triển khai bằng biến môi trường:

```powershell
$env:VPVA_TEACHING_PASSWORD = "mat-khau-cua-ban"
python main.py
```

Không lưu mật khẩu, token hoặc thông tin nhạy cảm trực tiếp trong repository.
