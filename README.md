**YT Downloader**  
Trình tải video/audio YouTube cho Windows với giao diện kiểu IDM: danh sách tải, thanh tiến trình, tốc độ, thời gian còn lại cập nhật liên tục. Dán link một video hoặc cả playlist là tải được. Xây dựng trên [yt-dlp và Tkinter.](https://github.com/yt-dlp/yt-dlp "https://github.com/yt-dlp/yt-dlp")  
**Tính năng**  
- Dán link video hoặc playlist; playlist được thêm vào hàng đợi, có thể tạo thư mục riêng và đánh số thứ tự.  
- Theo dõi thời gian thực: dung lượng, đã tải, tốc độ, thời gian còn lại, thời gian đã chạy, trạng thái ghép/chuyển đổi, tổng tốc độ.  
- Định dạng: video 360p đến 4K hoặc tốt nhất; audio MP3, M4A hoặc bản gốc.  
- Chế độ **Tương thích TV / thiết bị cũ**: chỉ lấy H.264 + AAC (MP4), không AV1/VP9, tối đa 1080p.  
- Tải đồng thời nhiều video, có tạm dừng, tiếp tục, thử lại; tải dở được giữ lại và tiếp tục từ file .part.  
- **Tự khắc phục lỗi:** tự thử lại tối đa 6 lần khi gặp 403, hết thời gian chờ, mất kết nối; tự bỏ link cũ và lấy link mới khi tốc độ quá chậm.  
- Khi YouTube yêu cầu "xác minh không phải bot", app tự thử các client dự phòng của YouTube; nếu bị chặn kéo dài thì nghỉ và dừng hàng đợi.  
- Video đơn lẻ được thêm thẳng vào hàng đợi, không gửi thêm yêu cầu phân tích (giảm nguy cơ bị chặn).  
- Không dùng cookies hay tài khoản của người dùng.  
**Yêu cầu**  
| | | |  
|-|-|-|  
| **Thành phần** | **Dùng để** | **Ghi chú** |   
| Windows 10/11 | Chạy file .exe | Chạy từ mã nguồn thì Windows, macOS, Linux đều được |   
| [Deno](https://deno.com "https://deno.com") | yt-dlp giải thử thách của YouTube | **Đã kèm trong bản release** (deno.exe). Thiếu thì dễ tải chậm hoặc lỗi 403 |   
| [FFmpeg](https://ffmpeg.org "https://ffmpeg.org") | Ghép video+audio, xuất MP3, độ phân giải cao | Khuyến nghị. Không có thì video giới hạn khoảng 720p và không có MP3 |   
   
**Cài đặt nhanh (bản release)**  
1. Vào trang **Releases** của repo, tải file YT-Downloader-vX.Y.Z-win64.zip và giải nén.  
2. Giữ YT Downloader.exe và deno.exe**trong cùng một thư mục**.  
3. Cài FFmpeg, chọn một trong hai cách:  
  - Chạy winget install ffmpeg, hoặc  
  - Đặt ffmpeg.exe và ffprobe.exe cạnh file exe.  
4. Chạy YT Downloader.exe.  
*Windows Defender hoặc phần mềm diệt virus đôi khi báo nhầm file đóng gói bằng PyInstaller. Nếu gặp, hãy thêm file vào danh sách ngoại lệ hoặc tự build từ mã nguồn (xem bên dưới).*  
**Chạy từ mã nguồn**  
Cần Python 3.9 trở lên (đã gồm Tkinter).  
pip install -r requirements.txt  
 python yt_downloader.py  
   
Cài Deno: winget install DenoLand.Deno (Windows) hoặc xem [deno.com cho hệ điều hành khác.](https://deno.com "https://deno.com")  
**Hướng dẫn sử dụng**  
1. Dán link vào ô **Link** (có thể dán nhiều link cách nhau bằng dấu cách), bấm  **Thêm & Tải** hoặc Enter.  
2. Với playlist, app hỏi xác nhận số video rồi thêm tất cả vào danh sách.  
3. Chọn dòng để xem chi tiết ở khung bên dưới; nút **Chọn tất cả** hoặc Ctrl+A để thao tác hàng loạt.  
| | |  
|-|-|  
| **Tuỳ chọn** | **Ý nghĩa** |   
| Định dạng | Chất lượng video hoặc loại audio |   
| Lưu vào | Thư mục lưu file |   
| Tải đồng thời | Số video tải cùng lúc (1 đến 8) |   
| Luồng/file | Số luồng tải song song cho mỗi file. Chỉ có tác dụng với video tải theo từng đoạn (DASH/HLS) |   
| Tải lại nếu chậm hơn (KB/s) | Tốc độ thấp hơn mức này liên tục hơn 10 giây thì app lấy link mới và tải tiếp. 0 là tắt |   
| Nghỉ giữa video (giây) | Giãn cách giữa các video để tránh bị YouTube chặn |   
| Playlist → thư mục riêng | Tạo thư mục theo tên playlist và đánh số 01, 02... |   
| Link có list=... → chỉ tải 1 video | Link video kèm tham số playlist chỉ tải video đó |   
| Tương thích TV | Chỉ H.264 + AAC, không AV1/VP9, tối đa 1080p |   
| Kết nối IP | Tự động, Chỉ IPv4 hoặc Chỉ IPv6. Có thể chỉ một trong hai bị YouTube chặn; nút **Kiểm tra mạng** thử riêng từng đường và gợi ý nên chọn đường nào |   
   
Cài đặt được lưu tại C:\Users\<tên bạn>\.yt_idm_downloader.json.  
**Xử lý sự cố**  
| | |  
|-|-|  
| **Hiện tượng** | **Cách xử lý** |   
| Google/YouTube bắt giải captcha dù dùng tab ẩn danh | IP mạng của bạn đang bị gắn cờ (IP dùng chung của nhà mạng, VPN/proxy, hoặc thiết bị khác trong mạng gửi nhiều yêu cầu). Bấm **Kiểm tra mạng**; nếu cả hai đường bị chặn thì đổi IP (khởi động lại modem, phát 4G từ điện thoại) hoặc đợi |   
| Tải chậm hoặc lỗi 403 | Kiểm tra dòng cảnh báo vàng ở cuối cửa sổ; đảm bảo deno.exe nằm cạnh file exe. App tự thử lại, nếu vẫn lỗi hãy cập nhật (xem bên dưới) |   
| "Sign in to confirm you're not a bot" | YouTube tạm chặn mạng của bạn (thường do tải dồn dập, dùng VPN/proxy hoặc IP bị gắn cờ). App tự thử các client dự phòng; nếu vẫn bị chặn: đợi vài phút, tắt VPN/proxy, đổi mạng (phát 4G từ điện thoại) hoặc khởi động lại modem để đổi IP, đặt Tải đồng thời = 1 và tăng Nghỉ giữa video lên 5 đến 10 giây, rồi Chọn tất cả và Tiếp tục. Không cần cookies |   
| Chỉ tải được khoảng 720p, không có MP3 | Chưa có FFmpeg |   
| Video không phát được trên TV | Bật **Tương thích TV** rồi tải lại; TV cũ thường không giải mã được AV1 |   
| Lỗi sau khi YouTube thay đổi | Cập nhật yt-dlp: pip install -U "yt-dlp[default]", hoặc build lại exe bằng bản yt-dlp mới nhất |   
   
Khi xóa một mục đang tải dở, file .part vẫn còn trong thư mục; bạn tự xóa nếu không cần.  
**Tự build file exe**  
Trên Windows, cần Python trong PATH. Chạy:  
build_exe.bat  
   
Script sẽ cài package, tải deno.exe nếu máy chưa có, đóng gói bằng PyInstaller và đặt kết quả vào thư mục dist\. Nếu để sẵn ffmpeg.exe và ffprobe.exe cạnh build_exe.bat, chúng sẽ được nhúng vào exe.  
**Phát hành (dành cho người bảo trì)**  
Repo có sẵn workflow GitHub Actions (.github/workflows/release.yml). Khi đẩy một tag bắt đầu bằng v, workflow tự build trên Windows và tạo bản release kèm file zip:  
git tag v1.0.0  
 git push origin v1.0.0  
   
Mỗi bản build lấy yt-dlp mới nhất. Khi YouTube đổi cách chặn và bản cũ bị lỗi, chỉ cần đẩy một tag mới (ví dụ v1.0.1) để có bản cập nhật.  
**Lưu ý sử dụng**  
Công cụ này dành cho việc tải nội dung bạn có quyền tải (video của chính bạn, nội dung được cấp phép, sử dụng cá nhân hợp pháp). Bạn tự chịu trách nhiệm tuân thủ điều khoản dịch vụ của YouTube và luật bản quyền tại nơi bạn sống.  
**Ghi công**  
- [yt-dlp và ](https://github.com/yt-dlp/yt-dlp "https://github.com/yt-dlp/yt-dlp")[yt-dlp-ejs](https://github.com/yt-dlp/ejs "https://github.com/yt-dlp/ejs")  
- [FFmpeg](https://ffmpeg.org "https://ffmpeg.org")  
- [Deno](https://deno.com "https://deno.com")  
