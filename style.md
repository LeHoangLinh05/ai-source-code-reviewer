Dưới đây là tài liệu quy chuẩn giao diện và trải nghiệm người dùng (`style.md`) được viết lại toàn diện nhằm định hình chính xác ngôn ngữ thiết kế cho **RepoGuard AI**.

Tài liệu được cập nhật dựa trên lộ trình phát triển cuốn chiếu (vertical slice), đồng thời tối ưu hóa các thành phần phức tạp như: Hệ thống đo điểm bảo mật, Trình xem code lồng trong Drawer, và bảng điều khiển trace log của AI Agent (`/ai-debug`).

---

# RepoGuard AI — Quy Chuẩn Giao Diện & Trải Nghiệm Người Dùng (UI/UX Design System)

Tài liệu này đóng vai trò là khung tham chiếu (Design System) nhất quán cho toàn bộ giao diện Frontend (Next.js 15, TailwindCSS, shadcn/ui) của dự án RepoGuard AI.

Với định hướng sản phẩm là công cụ phân tích bảo mật dành cho lập trình viên và kỹ sư an toàn thông tin, giao diện được thiết kế theo phong cách tối giản, tập trung vào mật độ thông tin cao, trực quan hóa dữ liệu và hiển thị minh bạch quá trình vận hành của AI.

---

## 1. TRIẾT LÝ THIẾT KẾ (DESIGN PHILOSOPHY)

Giao diện RepoGuard AI được định hình dựa trên 4 nguyên lý cốt lõi:

*   **Tập trung vào dữ liệu (Information Density Over Decoration):** Lập trình viên ưu tiên hiệu năng và tốc độ xử lý thông tin. Hệ thống hạn chế tối đa các chi tiết trang trí không cần thiết, tối ưu hóa khoảng cách dòng để hiển thị nhiều mã nguồn và dữ liệu log nhất có thể trên một màn hình.
*   **Ưu tiên tính chuyên nghiệp trung tính (Neutral-First Professional UI):** Giao diện không được lạm dụng các mảng màu xanh/đỏ/tím cho trạng thái. Nền, card, bảng, sidebar và button dùng Slate/Ink làm chủ đạo. Màu trạng thái chỉ xuất hiện như tín hiệu phụ rất nhỏ, ví dụ chấm trạng thái, viền mảnh, hoặc icon đơn sắc; tuyệt đối không phủ nền màu rực trên badge, row, card hoặc CTA.
*   **Minh bạch hóa hộp đen AI (Explainable AI Engine):** Tránh tạo cảm giác mơ hồ về các phân tích của AI. Giao diện phải bóc tách rõ nguồn gốc lỗi: đến từ Static Analyzer (Ruff/Bandit/ESLint) hay do AI tự suy luận, kèm theo các bước gọi công cụ (tool call traces) và tài liệu đối chiếu (RAG Context).
*   **Chuyển đổi trạng thái mượt mà (Graceful Degraded & Progressive Loading):** Do tích hợp cơ chế realtime SSE và xử lý nền (Celery), giao diện được thiết kế để xử lý linh hoạt các trạng thái trung gian: từ hàng đợi chờ, đang phân tích từng phần cho đến khi hoàn thành hoặc có lỗi xảy ra.

---

## 2. HỆ THỐNG MÀU SẮC (COLOR SYSTEM)

Hệ thống màu sử dụng bảng mã màu tối (Dark Mode) làm chủ đạo nhằm giảm mỏi mắt cho lập trình viên khi làm việc trong thời gian dài.

### 2.0 Nguyên Tắc Màu Trung Tính (Neutral-First Rule)

RepoGuard AI là công cụ kỹ thuật, không phải dashboard marketing. Vì vậy giao diện phải có cảm giác chắc, nhất quán và ít "nhựa":

*   **Không tô màu theo cảm xúc:** Không dùng badge xanh lá/đỏ/cam/xanh dương dạng nền đậm cho các trạng thái thường gặp như `COMPLETED`, `PENDING`, `AI_REVIEWING`.
*   **Slate là hệ màu chính:** Active navigation, CTA, bảng, filter, metric và card đều dùng Slate/Ink. Primary button ưu tiên `slate-200` trên nền tối hoặc `slate-800` cho secondary action.
*   **Màu semantic là tín hiệu nhỏ:** Nếu cần phân biệt severity, chỉ dùng viền mảnh, dot nhỏ, icon đơn sắc, hoặc chữ đậm hơn trong cùng thang Slate. Không dùng background `rose-500/15`, `emerald-500/15`, `blue-500/15` trên badge/table row.
*   **Destructive action phải tiết chế:** `Logout all`, delete, revoke token có thể dùng rose rất tối (`rose-950/30`, `border-rose-900/60`) nhưng không dùng nút đỏ rực toàn khối.
*   **Biểu đồ ưu tiên thang xám:** Gauge, bar, pie chart mặc định dùng Slate ramp. Chỉ dùng màu semantic khi người dùng cần can thiệp khẩn cấp, và khi dùng phải giới hạn ở stroke/marker thay vì mảng nền lớn.

### 2.1 Màu Nền & Màu Nền Chức Năng (Base & Surface Colors)

Hệ màu nền tuân thủ chặt chẽ dải màu **Slate** của TailwindCSS để tạo chiều sâu cho giao diện:

```json
{
  "theme": {
    "colors": {
      "background": "#020617", // Slate 950 - Nền sâu nhất (App Background)
      "card": "#0f172a",       // Slate 900 - Nền của các khối nội dung, card, sidebar
      "popover": "#0f172a",    // Slate 900 - Menu thả xuống, Tooltip, Dropdown
      "border": "#1e293b",     // Slate 800 - Đường kẻ viền phân cách mặc định
      "input": "#1e293b",      // Slate 800 - Đường viền của form, ô nhập liệu
      "primary": {
        "DEFAULT": "#e2e8f0",  // Slate 200 - CTA chính trung tính trên nền tối
        "foreground": "#020617"
      },
      "secondary": {
        "DEFAULT": "#334155",  // Slate 700 - Nút bấm phụ, trạng thái không kích hoạt
        "foreground": "#f8fafc"
      },
      "muted": {
        "DEFAULT": "#1e293b",  // Slate 800 - Nền các trạng thái disabled hoặc thông tin phụ
        "foreground": "#94a3b8" // Slate 400 - Màu chữ phụ (Subtext)
      }
    }
  }
}
```

### 2.2 Màu Sắc Định Danh Lỗi (Semantic & Severity Badges)

Phân loại mức độ nghiêm trọng bắt buộc phải đồng bộ, nhưng **không được đổ màu nền rực**. Badge mặc định dùng nền `background` hoặc `slate-900`, chữ Slate và viền Slate; severity được phân biệt bằng độ sáng/độ dày viền hoặc dot nhỏ.

| Mức độ nghiêm trọng | Tailwind Class Khuyến Nghị | Mã HEX | Trường hợp sử dụng |
| :--- | :--- | :--- | :--- |
| **Critical** | `border-slate-400 text-slate-50` | `#e2e8f0` | Các lỗ hổng bảo mật nghiêm trọng (SQL Injection, Hardcoded Secrets). |
| **High** | `border-slate-500 text-slate-100` | `#cbd5e1` | Lỗi logic nghiêm trọng, bug có khả năng gây crash hệ thống ở môi trường Production. |
| **Medium** | `border-slate-600 text-slate-200` | `#94a3b8` | Lỗi hiệu năng (N+1 queries, không đóng kết nối), code smell nặng. |
| **Low** | `border-slate-700 text-slate-300` | `#64748b` | Vi phạm tiêu chuẩn code (Style guide, quy tắc đặt tên, linting format). |
| **Info** | `border-slate-800 text-slate-400` | `#475569` | Gợi ý cải tiến kiến trúc, các thông số cấu hình mang tính chất tham khảo. |
| **Success** | `border-slate-500 text-slate-100` | `#cbd5e1` | Đạt chỉ số an toàn, phân tích hoàn thành thành công không phát hiện lỗi. |

---

## 3. HỆ THỐNG PHÔNG CHỮ & KIỂU CHỮ (TYPOGRAPHY)

*   **Phông chữ hệ thống (Sans-serif):** Ưu tiên `Geist Sans` hoặc `Inter` để đảm bảo ký tự hiển thị sắc nét ở kích thước nhỏ (12px).
*   **Phông chữ mã nguồn (Monospace):** Bắt buộc sử dụng `Geist Mono` hoặc `Fira Code` nhằm hỗ trợ kỹ thuật hiển thị liên kết ký tự (font ligatures), giúp dễ dàng đọc các toán tử so sánh hoặc cú pháp đặc thù của code.

### Tỷ Lệ Kích Thước Chữ (Type Scale)

| Kích thước | Line Height | CSS/Tailwind Class | Mục đích sử dụng |
| :--- | :--- | :--- | :--- |
| **12px (0.75rem)** | 16px | `text-xs font-medium` | Nhãn mức độ nghiêm trọng, thông tin dòng code, metadata của tệp. |
| **14px (0.875rem)** | 20px | `text-sm` | Khối mô tả lỗi, đề xuất sửa đổi, nội dung dòng code trong Viewer. |
| **16px (1rem)** | 24px | `text-base` | Nhãn form, giá trị cấu hình, nội dung đoạn văn chính. |
| **18px (1.125rem)** | 28px | `text-lg font-semibold` | Tiêu đề các phần phụ, tiêu đề card phân tích nhỏ. |
| **20px (1.25rem)** | 28px | `text-xl font-bold` | Tiêu đề của Drawer chi tiết, tiêu đề các khối chính. |
| **24px (1.5rem)** | 32px | `text-2xl font-extrabold` | Tiêu đề chính của màn hình Dashboard, Report Overview. |
| **32px (2rem)** | 36px | `text-3xl font-black` | Điểm số tổng quan (Overall Score), số lượng tổng lỗi nghiêm trọng. |

---

## 4. CẤU TRÚC BỐ CỤC CHUẨN (LAYOUT ARCHITECTURE)

Giao diện ứng dụng sử dụng cấu trúc chia khung cố định nhằm duy trì trải nghiệm tập trung.

### 4.1 Layout Dashboard Tổng Thể

```
┌────────────────────────────────────────────────────────────────────────┐
│  Navbar (Logo, Search, Notification Badge, User Profile)               │
├─────────────┬──────────────────────────────────────────────────────────┤
│             │  Main Scroll Container (p-6)                             │
│  Sidebar    │  ┌────────────────────────────────────────────────────┐  │
│  (w-64)     │  │  Page Title & Actions                              │  │
│             │  ├────────────────────────────────────────────────────┤  │
│  Navigation │  │                                                    │  │
│             │  │  Content Grid (Cards / Tables / Charts)            │  │
│  Active     │  │                                                    │  │
│  Indicators │  │                                                    │  │
│             │  └────────────────────────────────────────────────────┘  │
└─────────────┴──────────────────────────────────────────────────────────┘
```

*   **Thanh điều hướng bên trái (Sidebar):** Cố định chiều rộng ở `w-64` trên màn hình desktop (`lg` trở lên). Trên màn hình thiết bị di động hoặc máy tính bảng, Sidebar tự động thu gọn thành Drawer trượt (Sheet component của shadcn/ui).
*   **Vùng nội dung chính (Main Content Scroll):** Thiết lập `overflow-y-auto` độc lập để ngăn cuộn toàn màn hình, đảm bảo thanh điều hướng bên trái và thanh tiêu đề trên cùng luôn cố định khi duyệt danh sách lỗi dài.

---

## 5. QUY CHUẨN CÁC THÀNH PHẦN ĐẶC THÙ (COMPONENT SPECIFICATIONS)

### 5.1 Tiến Trình Phân Tích Realtime (SSE Progress Tracker)

Thành phần hiển thị tiến trình phân tích theo thời gian thực nhận luồng sự kiện từ SSE, được thiết kế theo dạng danh sách các bước có trạng thái đồng bộ:

```
[✓] Bước 1: CLONING REPOSITORY .......... Hoàn thành (0.5s)
[▶] Bước 2: CHẠY LINTING & STATIC SCANS .. Đang xử lý (Ruff: 12 lỗi tìm thấy)
[ ] Bước 3: PHÂN TÍCH NGỮ CẢNH BẰNG AI ... Đang chờ
```

*   **Trạng thái Đang Chờ (Pending):** Text màu `slate-600`, icon hình tròn rỗng (`border-slate-700`).
*   **Trạng thái Đang Xử Lý (Running):** Text màu `slate-100`, icon xoay nhẹ (`animate-spin border-slate-400 border-t-transparent`), nền giữ trung tính (`bg-slate-900` hoặc `bg-background`) và không dùng glow màu.
*   **Trạng thái Hoàn Thành (Completed):** Text màu `slate-100`, icon check (`✓`) màu `slate-300`.
*   **Trạng thái Thất Bại (Failed):** Text màu `slate-200`, icon dấu nhân (`✕`) màu `slate-300`. Chi tiết lỗi kỹ thuật hiển thị ở dòng ngay dưới bằng phông Monospace cỡ `text-xs`; nếu cần destructive tone thì chỉ dùng `border-rose-900/60` hoặc `rose-950/30`.

### 5.2 Trình Biểu Diễn Điểm Số (Radial Score Gauges)

Điểm số bảo mật, khả năng bảo trì và hiệu năng được trực quan hóa bằng Radial Bar Chart (Recharts), hiển thị dải điểm từ `0.0` đến `10.0`. Màu sắc của biểu đồ tự động biến đổi theo giá trị điểm:

*   **Dưới 5.0 (Kém):** Dải màu `slate-500` (`#64748b`), label hiển thị rõ bằng chữ.
*   **Từ 5.0 đến 7.9 (Trung bình):** Dải màu `slate-400` (`#94a3b8`).
*   **Từ 8.0 đến 10.0 (Tốt):** Dải màu `slate-200` (`#e2e8f0`).

### 5.3 Trình Xem Code Chi Tiết (Code Snippet Viewer)

Trình xem code được nhúng bên trong Drawer trượt từ cạnh phải màn hình (`Sheet` Component) khi người dùng nhấp vào một Issue bất kỳ.

```
  Line  Code Content
  40 |  def query_user_profile(user_id: str):
  41 |      # SQL injection vulnerability detected by Bandit & AI
  42 | [!]  query = f"SELECT * FROM profiles WHERE id = {user_id}"  <─ Phủ màu bg-slate-800/50, border-l-slate-300
  43 |      return db.execute(query).fetchone()
```

*   **Syntax Highlighting:** Sử dụng Prism.js hoặc Shiki với theme tối giản (One Dark Pro / Dracula).
*   **Đánh Dấu Dòng Lỗi (Line Highlighting):** Dòng xảy ra lỗi dùng nền trung tính nổi nhẹ (`bg-slate-800/50`), viền biên trái dày `border-l-4 border-l-slate-300` và một icon cảnh báo nhỏ `[!]` nằm trước số dòng. Không dùng nền đỏ phủ toàn dòng.
*   **Số Dòng (Gutter):** Hiển thị số dòng cố định màu `slate-600` ở lề trái, thiết lập thuộc tính CSS `user-select: none` để người dùng không bôi đen nhầm số dòng khi copy mã nguồn.
*   **Vùng Đề Xuất Sửa Đổi (Fix Diff Box):** Ngay dưới trình xem code, hiển thị tab so sánh (Diff block) giữa context hiện tại và code đề xuất. Cả hai dùng Slate ramp; phân biệt bằng nhãn `current context` / `proposed fix` và độ sáng của `border-l`, không dùng đỏ/xanh lá làm nền.

### 5.4 Bảng Điều Khiển Trace Log AI (`/ai-debug`)

Để phục vụ kiểm thử và chứng minh tính minh bạch của AI Agent, trang `/ai-debug` hiển thị tiến trình suy luận (Reasoning loop) dưới dạng một Timeline dọc:

*   **Hộp suy nghĩ (Reasoning Thought Block):** Hiển thị dưới dạng khối văn bản chữ nghiêng màu `slate-400` bên trong một khung bo góc đứt nét (`border-dashed border-slate-700 bg-slate-900/50`).
*   **Lượt gọi công cụ (Tool Calls):** Thiết kế dạng các Collapsible Cards xếp chồng. Đầu card hiển thị tên công cụ (ví dụ: `search_coding_standard`) kèm theo thẻ chỉ số thời gian xử lý trung tính (`border border-slate-700 bg-background text-slate-300 px-1.5 py-0.5 rounded text-xs`).
*   **Nội dung bên trong Card khi mở rộng:**
    *   *Tham số truyền vào (Arguments):* Khối JSON định dạng chuẩn có tính năng copy nhanh.
    *   *Dữ liệu phản hồi (Response/RAG Retrieval):* Hiển thị danh sách các đoạn tài liệu tìm thấy từ ChromaDB kèm theo chỉ số tương đồng (Similarity Score, ví dụ: `0.89`).

---

## 6. TRẠNG THÁI GIAO DIỆN & TƯƠNG TÁC CHUYỂN ĐỘNG

### 6.1 Trạng Thái Đang Tải (Skeleton Loading)

Tuyệt đối không sử dụng màn hình tải toàn trang (Full-page spinner). Thay vào đó, áp dụng cơ chế Skeleton riêng biệt cho từng thành phần dữ liệu chưa tải xong.

*   **Bảng danh sách lỗi (Issue Table Skeleton):** Render một khung bảng giả định có từ 3 đến 5 dòng với các vệt xám dạng dải chữ nhật có bo góc (`bg-slate-800 rounded animate-pulse`).
*   **Biểu đồ điểm số (Chart Skeleton):** Thay thế biểu đồ tròn bằng các hình tròn rỗng mờ (`border-2 border-slate-800 rounded-full animate-pulse`).

### 6.2 Hiệu Ứng Chuyển Động (Transitions & Micro-interactions)

Chuyển động trong ứng dụng được thiết kế tối giản, tập trung vào tốc độ phản hồi nhanh để tạo cảm giác mượt mà:

*   **Tốc độ chuyển động mặc định (Duration):** `150ms` đến `200ms`.
*   **Hàm mượt (Easing):** Sử dụng hàm cubic-bezier chuẩn của Tailwind CSS (`ease-in-out`).
*   **Hover States (Nút bấm, Hàng trong bảng):** Tăng nhẹ độ sáng nền (`hover:bg-slate-800/80 transition-colors duration-150`) và chuyển đổi con trỏ chuột sang trạng thái chỉ định (`cursor-pointer`).
*   **Hiệu ứng mở Drawer chi tiết:** Sử dụng thuộc tính `transition-transform duration-200 ease-out` để Drawer trượt êm ái từ lề phải vào (`translate-x-full` sang `translate-x-0`).

---

## 7. TIÊU CHUẨN TIẾP CẬN (ACCESSIBILITY - A11Y)

Hệ thống tuân thủ các quy tắc thiết kế dành cho lập trình viên có các hạn chế về khả năng tiếp cận:

*   **Độ Tương Phản Màu Chữ (Contrast Ratio):** Đạt tiêu chuẩn tối thiểu **WCAG AA** (tỷ lệ tương phản `4.5:1` đối với văn bản thường và `3:1` đối với văn bản lớn). Chữ màu xám phụ (`muted.foreground`) không được sáng dưới dải màu Slate 400 trên nền Slate 950.
*   **Điều Hướng Bằng Bàn Phím:** Tất cả các phần tử có thể tương tác (Nút bấm, Form nhập liệu, Tab chọn, Thẻ mở rộng Drawer) phải hiển thị rõ ràng đường viền tập trung khi người dùng nhấn phím `Tab`:
    *   Sử dụng lớp: `focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950`.
*   **Nhãn Trợ Thính (Screen Readers):** Bổ sung thuộc tính `aria-label` cho toàn bộ các nút chỉ có biểu tượng icon (ví dụ: Nút đóng Drawer, nút copy code). Các tab trạng thái phân tích bảo mật phải có `role="tab"` và các thuộc tính `aria-selected` tương ứng để trình đọc màn hình dễ dàng nhận diện.
