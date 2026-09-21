"""Plate rectification shared by inference and reviewed training images."""
import numpy as np


def ordered_quad(points, cv2):
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    if not np.isfinite(points).all():
        raise ValueError('プレートの四隅が不正です。')
    center = points.mean(axis=0)
    angles = np.arctan2(points[:, 1]-center[1], points[:, 0]-center[0])
    points = points[np.argsort(angles)]
    points = np.roll(points, -np.argmin(points.sum(axis=1)), axis=0)
    if not cv2.isContourConvex(points.reshape(-1, 1, 2)):
        raise ValueError('プレートの四隅が不正です。')
    return points


def warp_plate(image, points, cv2, margin=0.0):
    """Rectify original pixels; optional output margin retains border characters."""
    if not np.isfinite(margin) or not 0 <= margin <= .1:
        raise ValueError('補正余白が不正です。')
    quad = ordered_quad(points, cv2)
    h, w = image.shape[:2]
    if (quad[:, 0].min() < 0 or quad[:, 1].min() < 0 or
            quad[:, 0].max() > w-1 or quad[:, 1].max() > h-1):
        raise ValueError('プレートの四隅が画像範囲外です。')
    tl, tr, br, bl = quad
    width = round(max(np.linalg.norm(tr-tl), np.linalg.norm(br-bl)))
    height = round(max(np.linalg.norm(bl-tl), np.linalg.norm(br-tr)))
    if width < 40 or height < 18 or not 1.1 <= width / height <= 2.7:
        raise ValueError('補正後のプレート寸法が不正です。')
    mx, my = round(width*margin), round(height*margin)
    target = np.float32([[mx, my], [width-1+mx, my],
                         [width-1+mx, height-1+my], [mx, height-1+my]])
    transform = cv2.getPerspectiveTransform(quad, target)
    return cv2.warpPerspective(image, transform, (width+2*mx, height+2*my),
                               flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def rectify_candidate(image, bbox, cv2):
    """Find a convex plate border near bbox and rectify its four actual corners.

    Prefer detector-aligned quadrilaterals to surrounding frames. A rotated
    bounding rectangle is not a substitute for missing perspective corners.
    Keep the unwarped ROI when neither the original nor contrast retry fits.
    """
    x1, y1, x2, y2 = bbox
    h, w = image.shape[:2]
    x1, y1, x2, y2 = max(0, int(x1)), max(0, int(y1)), min(w, int(x2)), min(h, int(y2))
    if x2 <= x1 or y2 <= y1:
        raise ValueError('プレート候補の範囲が不正です。')
    px, py = max(2, round((x2-x1)*.08)), max(2, round((y2-y1)*.08))
    left, top = max(0, x1-px), max(0, y1-py)
    roi = image[top:min(h, y2+py), left:min(w, x2+px)]
    # Bound contour work; the final warp still samples the original image.
    scale = min(1.0, 960 / max(roi.shape[:2]))
    search = cv2.resize(roi, (max(1, round(roi.shape[1]*scale)),
                               max(1, round(roi.shape[0]*scale)))) if scale < 1 else roi
    gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    factors = np.float32([roi.shape[1]/search.shape[1], roi.shape[0]/search.shape[0]])
    box_area = (x2-x1)*(y2-y1)
    for retry in range(2):
        if retry:
            gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 160)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:24]:
            area = cv2.contourArea(contour)
            if area < .3 * search.shape[0] * search.shape[1]:
                continue
            perimeter = cv2.arcLength(contour, True)
            for epsilon in (.015, .025, .04):
                polygon = cv2.approxPolyDP(contour, epsilon*perimeter, True)
                if len(polygon) != 4:
                    continue
                try:
                    points = ordered_quad(polygon, cv2)
                    quad_area = cv2.contourArea(points)
                    if quad_area <= 0 or not .85 <= area/quad_area <= 1.15:
                        continue
                    points = points*factors + np.float32([left, top])
                    lo, hi = points.min(axis=0), points.max(axis=0)
                    intersection = max(0, min(x2, hi[0])-max(x1, lo[0])) * max(0, min(y2, hi[1])-max(y1, lo[1]))
                    overlap = intersection / (box_area + np.prod(hi-lo) - intersection)
                    if overlap < .55:
                        continue
                    # Validate dimensions without allocating a warped image per proposal.
                    tl, tr, br, bl = points
                    qw = round(max(np.linalg.norm(tr-tl), np.linalg.norm(br-bl)))
                    qh = round(max(np.linalg.norm(bl-tl), np.linalg.norm(br-tr)))
                    if qw < 40 or qh < 18 or not 1.1 <= qw/qh <= 2.7:
                        continue
                    candidates.append((float(overlap), points))
                except ValueError:
                    continue
        if candidates:
            points = max(candidates, key=lambda item: item[0])[1]
            corrected = warp_plate(image, points, cv2, margin=.03)
            return corrected, points.tolist(), 'perspective'
    # An uncertain border is not a reason to invent a rotation.
    return roi, None, 'none'
