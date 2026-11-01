# Tray Counter — đếm tray theo code, trigger PLC

Luồng chạy mặc định của `python main.py`. Dùng `python main.py --legacy` để mở giao diện kiểm tape cũ.

## Chạy và cấu hình

```powershell
python -m pip install -r requirements.txt
python main.py
# Hoặc:
python -m tray_counter --settings tray_counter/config/settings.yaml
```

Đường dẫn trong `settings.yaml` và `registry.json` tính từ thư mục `tray_counter/` (thư mục cha của `config/`). Dữ liệu mặc định ở `tray_counter/data/`, tách khỏi `database.db` của giao diện cũ. Có thể đổi tên cột Excel, cách xử lý code trùng, thời gian chụp/xử lý/nạp model, số ngày giữ ảnh và cấu hình video trong settings.

`registry.json` ban đầu rỗng vì dự án hiện chưa có model target/other đã huấn luyện cho các code sản xuất. Tham khảo `config/registry.example.json`, đưa model ONNX thật vào `models/`, rồi thêm cấu hình tương ứng. Code chưa có model vẫn hiện trong danh sách Excel nhưng không được chọn. Các model kiểm tape `.pt` cũ không tự động chuyển thành model phân biệt mã sản phẩm.

## Chỉnh giao diện bằng Qt Designer

Form đang chạy là **`tray_counter/app/main_window.ui`**, định dạng Qt Designer / PyQt5. File `res/UI/mainwindow.ui` thuộc giao diện tape cũ, không được dùng ở chế độ đếm tray.

```powershell
designer tray_counter/app/main_window.ui
```

Nếu lệnh `designer` chưa có trong PATH, mở Qt Designer rồi dùng **File → Open** để chọn file trên. Có thể sửa chữ, placeholder, màu, font, kích thước, thứ tự widget và các layout bằng kéo thả; **Ctrl+R** xem trước, **Ctrl+S** lưu. Chạy lại chương trình để thấy thay đổi. Không cần sinh lại `main_window.py` hay chạy `pyuic5`; chương trình dùng `PyQt5.uic.loadUi` đọc form mỗi lần khởi động.

Giữ nguyên `objectName` của các widget mà controller dùng:

| Nhóm | objectName |
| --- | --- |
| Người thao tác, Excel | `actor`, `load_button`, `export_button` |
| Camera | `camera_kind`, `camera_source`, `camera_button` |
| Code | `filter`, `codes`, `select_button` |
| Trạng thái và ảnh | `readiness`, `summary`, `image` |
| Tray | `trays`, `recount_button`, `finish_button`, `skip_button` |
| PLC và log | `trigger`, `plc_signals`, `log` |

Kết nối signal/slot và logic nằm trong `tray_counter/app/main_window.py`. Trạng thái sẵn sàng, tổng số lượng, ảnh, chữ TRIGGER và trạng thái bật/tắt nút được cập nhật khi chạy. Bảng `codes` cần giữ 5 cột, bảng `trays` cần giữ 6 cột; mục trong `camera_kind` là tên loại camera mà chương trình dùng. Nếu đổi tên hoặc loại widget, thêm hành vi, đổi cấu trúc bảng hay tên loại camera thì cập nhật controller tương ứng.

Tên chung, phiên bản và tên file đóng gói nằm trong `app/metadata.py`. Khi build bằng `main.spec`, kết quả là `dist/TrayCounter/TrayCounter.exe`; form được chép ra `dist/TrayCounter/tray_counter/app/main_window.ui` để vẫn chỉnh được mà không cần build lại. Window title của form có thể sửa trực tiếp trong Designer.

## Vận hành

1. Nhập tên/mã người thao tác, bấm **Nạp Excel đầu ngày** (`.xlsx`, mặc định hai cột `code`, `qty`). Lưu code dạng text trong Excel để giữ số 0 đầu. File có dòng lỗi sẽ bị từ chối toàn bộ và báo số dòng. Công thức trong cột code/qty bị từ chối; cần xuất giá trị trước khi nạp.
2. Chọn nguồn camera. Với **SimCamera**, chọn thư mục ảnh gốc; ảnh được phát theo thứ tự tên, mỗi trigger lấy một ảnh, mặc định không lặp lại khi hết ảnh. HIK/Basler dùng serial ID; thông số exposure/gain cố định nằm trong settings. Webcam phục vụ thử nghiệm, thông số exposure phụ thuộc driver.
3. Lọc/chọn code và bấm **Chọn code**. Model được nạp và warm-up ở nền. Chờ dòng trạng thái báo sẵn sàng; model của code kế tiếp được nạp trước, cache giữ hai code.
4. PLC giữ TRIGGER=1 đến khi DONE=1. Khi dùng SimPLC: bấm **bật TRIGGER**, đọc kết quả, rồi bấm **hạ TRIGGER**. Giữ mức cao không chụp lần thứ hai. Sau khi mở chương trình cần có mức thấp để xác nhận sẵn sàng bắt cạnh lên.
5. Lặp lại cho các tray của cùng code. Kết quả từng tray chỉ kiểm sản phẩm đúng/lạ/không chắc chắn, không so với tổng Excel.
6. **Đếm lại tray vừa rồi** chỉ đặt yêu cầu: đưa lại tray lên băng tải và tạo trigger tiếp theo. Kết quả mới thay kết quả cũ, giữ cùng số thứ tự tray; lịch sử `replaced_by` vẫn được lưu.
7. **Xong code** so tổng các tray hiệu lực với Excel. Tổng bằng nhau, có ít nhất một tray, không còn foreign/lowconf/lỗi thì OK. Thiếu/thừa cần xác nhận và ghi người thao tác; code vẫn ở trạng thái Lệch. Chọn lại cùng code để bổ sung hoặc đếm lại tray cuối. Nếu tray lỗi nằm trước tray cuối, cần xử lý vật lý và đếm lại theo thứ tự phù hợp; chương trình không cho sửa tùy ý số đếm đã lưu.
8. Chỉ chuyển sang code khác sau OK hoặc quản lý bỏ qua code Lệch. Khi khởi động lại, chương trình khôi phục đơn trong ngày và chặn bỏ qua code còn dang dở. Nạp lại cùng danh sách sẽ giữ lịch sử; danh sách/số lượng khác trong cùng ngày bị từ chối để tránh ghi đè dữ liệu.
9. **Xuất báo cáo Excel** tạo ba sheet Orders, Trays, Events, gồm cả các kết quả đã bị thay. Chỉ tray chưa bị thay mới nằm trong tổng của Orders.

Quyền bỏ qua code lệch dùng mật khẩu cấu hình khi triển khai, không có mật khẩu mặc định:

```powershell
$env:TRAY_MANAGER_PASSWORD = "mat-khau-quan-ly"
python main.py
```

Mọi thao tác chọn/kết thúc/bỏ qua/đếm lại đều lưu người thao tác và thời gian. Cần dùng tài khoản/định danh quản lý phù hợp quy trình nội bộ khi triển khai tại máy.

## Handshake và lỗi

| Tín hiệu | Ý nghĩa |
| --- | --- |
| TRIGGER | PLC báo tray đã dừng, ổn định |
| BUSY | PC đang xử lý |
| DONE | Kết quả đã có, giữ đến khi TRIGGER xuống 0 |
| RESULT | 0 chưa có; 1 OK; 2 NG; 3 lỗi hệ thống/chưa sẵn sàng |
| HEARTBEAT | Đổi giá trị mỗi 500 ms, cấu hình được |
| ERR_CODE | 0 bình thường; 10 chưa sẵn sàng; 20 model; 30 camera; 40 lưu dữ liệu; 50 timeout; 60 PLC |

RESULT và ERR_CODE luôn ghi trước DONE. Trigger lúc chưa chọn code/model chưa sẵn sàng không chụp và trả RESULT=3. Camera/model/lưu ảnh lỗi không được trả OK. Hạ TRIGGER trước DONE làm hủy kết quả đang xử lý và yêu cầu nạp lại code. Tác vụ quá timeout bị cách ly; không dùng kết quả về muộn và dừng heartbeat cho đến khi tác vụ trả về. PLC phải có timeout riêng để giữ băng tải dừng khi PC không phản hồi.

Trước khi chụp, đơn được đánh dấu có tray đang xử lý trong SQLite. Nếu mất điện/lỗi ghi giữa chu kỳ, lần chọn lại code sẽ khôi phục một tray lỗi để yêu cầu đếm lại, tránh kết thúc OK dựa trên tổng cũ. Việc khôi phục này cũng được ghi log.

`core/plc.py` cung cấp `PLC`, `SimPLC`, và `MappedPLC` để gắn reader/writer của SDK PLC thật. Ứng dụng hiện chỉ khởi tạo SimPLC. Cần chốt hãng/dòng, giao thức và địa chỉ đủ sáu tín hiệu trước khi triển khai adapter thật; cấu hình loại PLC khác không được âm thầm chuyển sang giả lập. PC không điều khiển dừng/nhả băng tải.

## Hợp đồng model

Phương án A (`per_code`) yêu cầu hai lớp `target`, `other`, và tùy chọn lớp `tray`. Một detector chỉ có target không được coi là đủ để phát hiện lẫn mã. Phương án B (`shared`) yêu cầu detector `product`, backbone embedding ONNX riêng và ảnh mẫu là **crop sản phẩm đơn** trong `reference/CODE/`. Mean/std, kích thước embedding, cosine threshold và vùng không chắc chắn cấu hình theo code.

Cả hai dùng ONNX Runtime, input một tensor float32 `[1,3,H,W]` RGB, detector chuẩn hóa `/255`, letterbox toàn ảnh. Chọn định dạng output rõ ràng trong registry:

- `yolo_raw`: `[1,4+nc,N]`, tọa độ tâm xywh theo pixel input, class scores đã ở [0,1], không có objectness (export detect YOLOv8/YOLO11 thông thường).
- `xyxy`: `[1,N,6]`, x1/y1/x2/y2 theo pixel input, score, class_id (model đã tích hợp postprocessing).

Không tự đoán định dạng có sáu kênh vì hai lớp raw và output xyxy có thể bị nhầm. Metadata class names, nếu có, phải khớp thứ tự registry. Không hỗ trợ trực tiếp OBB/segmentation, YOLOv5 có objectness, FP16 hoặc output tọa độ chuẩn hóa. Model sai hợp đồng bị chặn ngay khi warm-up. Xem [ONNX Runtime Python](https://onnxruntime.ai/docs/api/python/api_summary.html) và [Ultralytics export](https://docs.ultralytics.com/modes/export).

Không dùng ROI cố định. `tray_mode: class` yêu cầu đúng một box tray đủ tin cậy. `tray_mode: contour` tìm contour tứ giác đầy đủ trong ảnh, đủ diện tích `tray_min_area`; phù hợp để thử trên khay tương phản rõ với nền. Cần đánh giá trên ảnh thật trước khi chọn contour cho sản xuất. Không tìm được tray sẽ báo lỗi; không tự coi toàn ảnh là tray. Box có tâm ngoài tray bị loại. Sản phẩm có score giữa conf_low và conf là lowconf; `other` đạt conf là foreign. Với shared, cosine sát sim_thr trong khoảng sim_margin là lowconf. Tray rỗng không báo OK.

## Lưu ảnh và video

Ảnh PNG gốc lưu tại `data/images/YYYYMMDD/CODE/seq_hhmmss_*.png`; tray NG/lỗi có ảnh vẽ box riêng nếu đã chụp được. Ảnh nguồn không lấy từ video nén. Chính sách giữ ảnh theo ngày chạy khi khởi động, không xóa bản ghi truy vết trong SQLite. Video bật bằng `video.enabled`, ghi MJPG AVI chia giờ trên thread riêng. Camera thật có một thread acquisition duy nhất, tự kết nối lại khi lỗi; recorder chỉ đọc frame cache, không gọi SDK chụp và không chặn luồng đếm. Camera đếm chờ frame được bắt đầu chụp sau yêu cầu để tránh lấy ảnh cũ. Với SimCamera, video giữ ảnh cuối cho đến trigger tiếp theo.

Video hiện không tự xóa; thời gian giữ và nguồn camera riêng/cùng camera cần chốt với sản xuất. Hỗ trợ hiện tại là PC lấy ảnh free-run sau trigger PLC; đấu trigger phần cứng trực tiếp vào camera cần adapter tương ứng và kiểm chứng SDK trên máy thật.

## Công cụ và kiểm thử

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python -m tray_counter.tools.build_registry tray_counter/models --version 2026-11-02 --tray-class
python -m tray_counter.tools.evaluate testdata/labels.json --registry tray_counter/config/registry.json --output evaluation.json
python -m tray_counter.tools.train_per_code datasets/codes.json --weights local_yolo11n.pt --output models_v1 --epochs 100
```

`build_registry` chỉ thêm code chưa có, warm-up kiểm tra model trước khi ghi, không ghi đè cấu hình shared hiện có. `train_per_code` nhận JSON `{ "CODE_0001": "CODE_0001/dataset.yaml" }`, yêu cầu dataset YOLO target/other (tùy chọn tray), weights YOLOv8/YOLO11 có sẵn; xuất ONNX và kiểm tra định dạng, không ghi đè model cũ. Model sau huấn luyện cần đánh giá trước khi đăng ký để chạy tại máy.

Manifest đánh giá là danh sách JSON, đường dẫn ảnh tính từ thư mục chứa manifest:

```json
[
  {"image": "A/normal.png", "code": "CODE_0001", "count": 12, "n_foreign": 0},
  {"image": "A/mixed.png", "code": "CODE_0001", "count": 11, "n_foreign": 1, "fault": true}
]
```

Báo cáo tách theo mode: đếm đúng, recall lẫn mã, NG nhầm, bỏ sót lỗi, lỗi hệ thống, p50/p95 thời gian suy luận và nạp model (gồm lần dùng cache). Exit code khác 0 khi có bỏ sót hoặc lỗi hệ thống; không suy ra đạt nghiệm thu chỉ từ exit code. Đánh dấu `fault: true` cho lỗi có chủ ý khác lẫn mã. Thiếu/thừa theo Excel phải kiểm thêm trên chuỗi nhiều tray vì từng tray không có số kỳ vọng trong luồng sản xuất.

Bộ pytest dùng SimCamera/SimPLC và ONNX mẫu tạo trong thư mục tạm để kiểm tra hợp đồng phần mềm. Các model mẫu chỉ là fixture kiểm thử. Chưa có số đo độ chính xác/độ trễ trên máy thật, bộ ảnh 10–15 code, hoặc 500 model sản xuất. Cần thu dữ liệu có mã dễ nhầm, đủ loại tray/ánh sáng/vị trí, so A/B trên cùng bộ ảnh, và đo song song đếm tay trước khi nghiệm thu.
