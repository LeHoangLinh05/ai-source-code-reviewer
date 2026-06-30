Dưới đây là thiết kế chi tiết cho file `style.md` (hoặc có thể đặt tên là `ui_ux_style.md`) tập trung toàn bộ vào quy chuẩn **UI/UX, Hệ Thống Nhận Diện Giao Diện (Design System), Trải Nghiệm Người Dùng** và các cấu hình thiết kế tương tác cho toàn bộ dự án **RepoGuard AI**.

---

# RepoGuard AI — Quy Chuẩn Giao Diện & Trải Nghiệm Người Dùng (UI/UX Style Guide)

Tài liệu này đóng vai trò là kim chỉ nam cho thiết kế giao diện (UI) và trải nghiệm người dùng (UX) của dự án RepoGuard AI. Tất cả các thành phần giao diện (Components), bố cục (Layouts), chuyển động (Transitions) và trạng thái hệ thống trên Frontend (Next.js 15, TailwindCSS, shadcn/ui) đều phải tuân thủ nghiêm ngặt các quy tắc dưới đây nhằm đảm bảo tính nhất quán và tối ưu hóa trải nghiệm cho lập trình viên.

---

## 1. TRIẾT LÝ THIẾT KẾ (DESIGN PHILOSOPHY)

Giao diện của RepoGuard AI được xây dựng dựa trên 4 nguyên tắc cốt lõi:

*   **Sự Rõ Ràng (Clarity is King):** Lập trình viên cần biết ngay lập tức đâu là lỗi nghiêm trọng nhất. Giao diện không được phép gây nhiễu thông tin bởi các yếu tố trang trí dư thừa.
*   **Cảm Giác An Toàn & Bảo Mật (Trust & Security):** Sử dụng tông màu tối sâu (Deep Dark), kết hợp với các đường viền sắc nét, độ tương phản cao, mang lại cảm giác chuyên nghiệp giống như một trung tâm vận hành an ninh mạng (SOC Dashboard).
*   **Sự Sống Động của AI (AI Explainability & Transparency):** Khi AI hoạt động, người dùng phải thấy được quá trình suy nghĩ (Reasoning) và gọi công cụ (Tool calling) thông qua hiệu ứng realtime mượt mà, tránh cảm giác "hộp đen" (Black box).
*   **Tối Giản Thao Tác (Low Cognitive Load):** Giảm thiểu tối đa số lần nhấp chuột để đi từ màn hình tổng quan đến dòng code bị lỗi chi tiết.

---

## 2. HỆ THỐNG MÀU SẮC CHI TIẾT (COLOR SYSTEM)

Hệ thống màu sử dụng thang đo mặc định của Tailwind CSS, được tối ưu hóa cho màn hình tối (Dark Mode làm chủ đạo).

### 2.1 Màu Nền & Màu Thương Hiệu (Core Colors)

```json
{
  "theme": {
    "colors": {
      "background": "#020617", // Slate 950 - Nền sâu nhất của ứng dụng
      "card": "#0f172a",       // Slate 900 - Nền của card, bảng, menu bên hông
      "popover": "#0f172a",    // Slate 900 - Nền của tooltip, dropdown, popover
      "border": "#1e293b",     // Slate 800 - Viền phân cách mặc định
      "input": "#1e293b",      // Slate 800 - Viền các thẻ input, select
      "primary": {
        "DEFAULT": "#7c3aed",  // Violet 600 - Nút bấm chính, AI elements, active states
        "foreground": "#ffffff"
      },
      "secondary": {
        "DEFAULT": "#475569",  // Slate 600 - Nút bấm phụ, trạng thái không hoạt động
        "foreground": "#f8fafc"
      },
      "muted": {
        "DEFAULT": "#1e293b",  // Slate 800 - Nền các thành phần bị vô hiệu hóa
        "foreground": "#94a3b8" // Slate 400 - Màu chữ phụ (Subtext)
      }
    }
  }
}
```

### 2.2 Màu Sắc Theo Ngữ Cảnh Bảo Mật (Semantic & Severity Colors)

Tuyệt đối tuân thủ bảng màu này trên toàn bộ đồ thị, bảng dữ liệu, và nhãn mức độ nghiêm trọng (Severity Badges).

| Mức Độ Severity | Màu Tailwind | Mã HEX | Trạng Thái Biểu Diễn |
| :--- | :--- | :--- | :--- |
| **Critical** | `rose-600` | `#e11d48` | Lỗi bảo mật nghiêm trọng (SQLi, hardcoded key) |
| **High** | `orange-500` | `#f97316` | Lỗi logic nguy cơ cao, bug có thể gây crash hệ thống |
| **Medium** | `yellow-500` | `#eab308` | Lỗi hiệu năng, code smell nặng, N+1 query |
| **Low** | `blue-500` | `#3b82f6` | Vi phạm tiêu chuẩn code, style guide lỗi nhỏ |
| **Info** | `slate-500` | `#64748b` | Các thông tin bổ sung, gợi ý cải tiến nhỏ |
| **Success** | `emerald-500` | `#10b981` | Hệ thống sạch, job chạy thành công |

---

## 3. HỆ THỐNG KIỂU CHỮ (TYPOGRAPHY)

*   **Font chữ Sans-Serif chính:** `Geist Sans` hoặc `Inter` (Sans-serif). Mang lại sự hiện đại, rõ nét ở các kích thước chữ nhỏ.
*   **Font chữ Monospace (Dành cho Code):** `Geist Mono` hoặc `Fira Code`. Hỗ trợ ligatures để lập trình viên dễ đọc cú pháp code.

### Cấu hình Scale Font Chữ

| Class Tailwind | Kích thước | Line Height | Ngữ cảnh sử dụng |
| :--- | :--- | :--- | :--- |
| `text-xs` | 12px (0.75rem) | 16px | Badge severity, nhãn biểu đồ, metadata |
| `text-sm` | 14px (0.875rem) | 20px | Nội dung bảng, mô tả lỗi, code viewer |
| `text-base` | 16px (1rem) | 24px | Đoạn văn bản chính, nhãn form đầu vào |
| `text-lg` | 18px (1.125rem) | 28px | Tiêu đề con (Sub-title), tiêu đề card nhỏ |
| `text-xl` | 20px (1.25rem) | 28px | Tiêu đề card chính, tiêu đề Drawer |
| `text-2xl` | 24px (1.5rem) | 32px | Tiêu đề trang chính (Dashboard, Report Overview) |
| `text-3xl` | 30px (1.875rem) | 36px | Điểm số tổng quát (Overall Score display) |

---

## 4. BỐ CỤC & GRID HỆ THỐNG (LAYOUT & RESPONSIVE GRID)

Giao diện áp dụng hệ thống lưới linh hoạt dựa trên Tailwind CSS Flexbox & CSS Grid.

### 4.1 Grid Breakpoints chuẩn
*   `sm`: 640px (Mobile dọc)
*   `md`: 768px (Tablet)
*   `lg`: 1024px (Laptop/Desktop nhỏ)
*   `xl`: 1280px (Desktop chuẩn)
*   `2xl`: 1536px (Màn hình lớn)

### 4.2 Cấu trúc trang Dashboard chuẩn
Trang Dashboard chính sử dụng bố cục cố định 2 phần:
*   **Sidebar (Cố định bên trái):** Chiều rộng `w-64` (256px) trên desktop, ẩn trên mobile và chuyển thành Drawer dạng trượt.
*   **Main Content (Khu vực nội dung bên phải):** Cuộn độc lập, padding mặc định là `p-4 sm:p-6 lg:p-8` để duy trì khoảng thở (white-space) cho giao diện dữ liệu lớn.

---

## 5. TRẠNG THÁI GIAO DIỆN & HIỆU ỨNG TƯƠNG TÁC (UI STATES)

Mọi thay đổi dữ liệu trên RepoGuard AI phải được biểu diễn qua các trạng thái trực quan, tuyệt đối không để giao diện bị "đứng hình" khi đang tải dữ liệu.

### 5.1 Trạng thái Đang Tải (Skeleton Loaders)
Không sử dụng biểu tượng Spinner quay vòng cho toàn bộ trang. Hãy sử dụng cấu trúc **Skeleton** tương tự với layout thực tế của thành phần đó để giảm cảm giác chờ đợi của người dùng.

*   **Nguyên tắc:** Skeleton của bảng phải có số hàng/cột tương đương dữ liệu thật, có hiệu ứng `animate-pulse` chậm rãi.

```typescript
// Ví dụ Skeleton cho hàng trong danh sách Issue
export const IssueRowSkeleton = () => (
  <div className="flex items-center space-x-4 py-3 px-4 border-b border-slate-800 animate-pulse">
    <div className="h-5 w-24 bg-slate-800 rounded" />
    <div className="flex-1 space-y-2">
      <div className="h-4 bg-slate-800 rounded w-3/4" />
      <div className="h-3 bg-slate-800 rounded w-1/2" />
    </div>
    <div className="h-6 w-16 bg-slate-800 rounded-full" />
  </div>
);
```

### 5.2 Trạng thái Trống (Empty States)
Khi chưa có dữ liệu (Ví dụ: chưa liên kết repository nào, không có lỗi bảo mật nào), giao diện phải hiển thị rõ ràng:
1.  Một hình minh họa tối giản (Icon dạng nét mờ).
2.  Tiêu đề ngắn gọn và thông điệp giải thích.
3.  Một nút kêu gọi hành động (Call to Action - CTA) cụ thể (Ví dụ: "Liên kết Repository ngay").

### 5.3 Trạng thái Lỗi (Error Boundaries)
Khi một API bị lỗi hoặc server không phản hồi:
*   Tuyệt đối không làm crash toàn bộ Dashboard. Chỉ vùng component bị lỗi hiển thị thông báo "Không thể tải dữ liệu" kèm nút **"Thử lại" (Retry)**.

---

## 6. QUY CHUẨN CÁC THÀNH PHẦN ĐẶC THÙ (COMPONENT DESIGN PATTERNS)

### 6.1 Thanh Tiến Trình Realtime (SSE Progress Tracker)
Khi Worker đang phân tích Repo, tiến trình được hiển thị dưới dạng timeline tuyến tính từng bước.
*   **Bước đã hoàn thành:** Icon check (`✓`) màu `emerald-500`, viền sáng xanh lá.
*   **Bước đang xử lý:** Icon vòng tròn xoay nhẹ (`animate-spin`), hiệu ứng viền phát sáng (Glow) màu `violet-600`.
*   **Bước đang chờ:** Chữ và viền màu `slate-600`.

### 6.2 Bộ Đo Điểm Số Bảo Mật (Score Gauges)
*   Sử dụng biểu đồ Radial Bar của Recharts để thể hiện điểm từ `0.0` đến `10.0`.
*   Màu sắc của dải điểm tự động thay đổi theo dải giá trị:
    *   `0.0 - 4.9`: Đỏ (`rose-600`) - Không đạt tiêu chuẩn an toàn.
    *   `5.0 - 7.9`: Vàng Cam (`amber-500`) - Cần cải thiện.
    *   `8.0 - 10.0`: Xanh Lá (`emerald-500`) - Codebase an toàn.

### 6.3 Trình Xem Code (Code Snippet Viewer)
*   **Đánh dấu dòng lỗi (Highlight Vulnerable Line):** Dòng code phát hiện lỗi phải có màu nền phủ đỏ mờ (`bg-rose-950/40`) và viền biên trái màu đỏ (`border-l-4 border-l-rose-600`).
*   **Số dòng (Line numbers):** Phải có số dòng cố định ở bên trái, không cho phép chọn text số dòng khi bôi đen code.
*   **Cú pháp:** Highlight chuẩn theo ngôn ngữ (Python, JS/TS) bằng PrismJS với chủ đề tối tối giản (Dracula hoặc One Dark Pro).

```
  39 |  def get_user(db, user_id):
  40 |      # Dòng thường
  41 |      query = f"SELECT * FROM users WHERE id = {user_id}"
> 42 |      return db.execute(query)  <── Highlight Đỏ (bg-rose-950/40 border-l-rose-600)
  43 |  
```

### 6.4 AI Debug & Tool Calling Timeline
Để hiển thị hoạt động của AI Agent một cách minh bạch:
*   Mỗi lượt gọi công cụ (tool call) được thiết kế như một khối dữ liệu thu gọn (Collapsible Card).
*   Đầu thẻ ghi rõ tên Tool (ví dụ: `search_coding_standard`) kèm theo thời gian xử lý (ví dụ: `245ms`).
*   Khi nhấn mở rộng: Hiển thị tham số đầu vào (JSON đầu vào) và kết quả tìm thấy (JSON đầu ra) dạng Codeblock gọn gàng.

---

## 7. CHUYỂN ĐỘNG & HIỆU ỨNG (MOTION & MICRO-INTERACTIONS)

Hiệu ứng chuyển động phải có tốc độ nhanh, dứt khoát nhằm tạo cảm giác ứng dụng phản hồi lập tức.

*   **Thời gian chuyển động mặc định (Duration):** `150ms` đến `200ms`.
*   **Hàm mượt (Easing):** Sử dụng `cubic-bezier(0.4, 0, 0.2, 1)` (mặc định là `ease-in-out` của Tailwind).
*   **Hover states:** Mọi nút bấm, hàng trong bảng có thể nhấp chuột phải có hiệu ứng chuyển màu nền nhẹ (`transition-colors duration-150`) và đổi con trỏ thành `cursor-pointer`.
*   **Drawer Slide:** Panel xem chi tiết Issue trượt từ bên phải màn hình vào phải có cấu hình chuyển động mượt mà bằng Framer Motion hoặc CSS Transitions chuẩn:
    *   *Mở:* `translate-x-0` (với transition mượt).
    *   *Đóng:* `translate-x-full`.

---

## 8. TIÊU CHUẨN TRUY CẬP (ACCESSIBILITY - A11Y)

RepoGuard AI là một công cụ lập trình chuyên nghiệp, do đó việc tiếp cận dễ dàng là bắt buộc:

*   **Độ tương phản (Contrast Ratio):** Toàn bộ văn bản phải có độ tương phản tối thiểu `4.5:1` so với nền tối (Đạt chuẩn WCAG AA). Không sử dụng các tông chữ xám quá mờ trên nền đen.
*   **Điều hướng bàn phím (Keyboard Navigation):** Mọi nút, liên kết và thẻ điều hướng (tabs) phải hiển thị đường viền tập trung rõ ràng (`focus-visible:ring-2 focus-visible:ring-violet-500`) khi điều hướng bằng phím `Tab`.
*   **Thẻ hỗ trợ (ARIA Attributes):** Các component tự tùy biến (Custom Drawers, Modals, Tabs) bắt buộc phải có đầy đủ các nhãn ARIA (`aria-expanded`, `aria-modal`, `aria-selected`) để hỗ trợ trình đọc màn hình cho người khiếm thị.