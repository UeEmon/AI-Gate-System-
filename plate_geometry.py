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


def warp_plate(image, points, cv2):
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
    target = np.float32([[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]])
    transform = cv2.getPerspectiveTransform(quad, target)
    return cv2.warpPerspective(image, transform, (width, height), flags=cv2.INTER_CUBIC)


def rectify_candidate(image, bbox, cv2):
    x1, y1, x2, y2 = bbox
    h, w = image.shape[:2]
    px, py = max(2, round((x2-x1)*.04)), max(2, round((y2-y1)*.04))
    left, top = max(0, x1-px), max(0, y1-py)
    roi = image[top:min(h, y2+py), left:min(w, x2+px)]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    contours, _ = cv2.findContours(cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 160),
                                   cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if area < .35 * roi.shape[0] * roi.shape[1]:
            continue
        polygon = cv2.approxPolyDP(contour, .025 * cv2.arcLength(contour, True), True)
        method = 'perspective'
        if len(polygon) != 4:
            rect = cv2.minAreaRect(contour)
            rect_area = rect[1][0] * rect[1][1]
            if not rect_area or area / rect_area < .85:
                continue
            polygon = cv2.boxPoints(rect)
            method = 'rotation'
        try:
            points = ordered_quad(polygon, cv2) + np.float32([left, top])
            corrected = warp_plate(image, points, cv2)
        except ValueError:
            continue
        return corrected, points.tolist(), method
    # An uncertain border is not a reason to invent a rotation.
    return roi, None, 'none'
