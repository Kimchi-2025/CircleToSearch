"""Instructions for clothing, footwear, and accessory virtual try-on requests."""



# Applied to every product category without changing the existing API signature.
# Image 2 may contain a catalog model: only the PRODUCT is a visual reference.
_COMMON_TRYON_RULES = (
    "Important reference separation: image 1 is the sole reference for the person, "
    "identity, anatomy, pose, scene and framing. Use image 2 ONLY for the product's "
    "appearance and how that product is worn; do not transfer the catalog model's "
    "face, head, hair, body, skin, identity, pose or background into image 1. "
    "Distinguish a head or face outside the original frame from facial details merely "
    "covered by an item inside the frame. If the head or face is outside image 1's "
    "frame, do not invent it, change the framing, or import it from image 2. If a face "
    "or head already exists inside image 1 but is partially covered by eyewear, a hat "
    "or another replaced item, naturally reconstruct ONLY the newly exposed local "
    "details (such as eyes behind opaque sunglasses or hair beneath a changed hat) "
    "when needed for a believable try-on. Such details are plausible estimates, not "
    "necessarily the person's real hidden features. Preserve visible facial features, "
    "head shape, expression and hairstyle wherever possible; never replace or invent "
    "an entire face, head or a different identity. "
    "However, do NOT omit or erase anatomy needed for a realistic try-on: when the new "
    "product exposes skin previously covered by clothing or an accessory, naturally "
    "reconstruct the necessary visible arms, hands, fingers, legs, feet or toes within "
    "the original frame. Match image 1's skin tone, proportions and plausible anatomy; "
    "keep the original pose as closely as possible. Do not introduce unrelated body "
    "parts or change the person's identity, framing or aspect ratio. "
)

_HEAD_ACCESSORY_RULES = (
    "For headwear or eyewear, apply the item only when the relevant head or face "
    "region exists within the original frame, even if partly obscured by the old item. "
    "Reconstruct locally concealed features only as needed to replace that item; "
    "never create an off-frame head or face to display the product. "
)

_EYEWEAR_REVEAL_RULES = (
    "When replacing opaque sunglasses with clear-lens eyeglasses, naturally render "
    "both eyes and the nearby skin that become visible behind the new lenses. "
    "Estimate obscured eye details consistently with the visible face in image 1; "
    "do not leave empty, missing or malformed eyes, and do not borrow eyes, facial "
    "features or identity from the catalog model in image 2. If the eyes or entire "
    "face are outside the frame rather than merely covered by sunglasses, do not "
    "invent them or shift the framing to reveal them. "
)

def build_tryon_prompt(product_name: str, brand: str | None, product_type: str,
                       description: str, box: list[float] | None, width: int, height: int,
                       region: str = 'upper') -> str:
    if box is not None:
        left, top, right, bottom = box
        position = (f"x={left / width:.2f}..{right / width:.2f}, "
                    f"y={top / height:.2f}..{bottom / height:.2f}")
    location = f"near the selected area ({position})" if box is not None else "in image 1"
    if region == 'belt':
        return (
            "Image 1 is the original person photograph. Image 2 is the catalog photo of the new "
            "belt. Replace the person's existing belt with the product in image 2, or add it "
            f"naturally at the visible waist {location} if no belt is worn. "
            f"Selected item type: {product_type}. Catalog name: {product_name}. "
            f"Brand, if known: {brand or 'unknown'}. Original item description: {description}. "
            "Preserve the reference buckle shape, color, leather or other material, and belt "
            "width. Fit the belt naturally through visible belt loops and along the trouser "
            "waistline, with appropriate perspective and shadows. Keep the existing top, lower "
            "clothing, face, body shape, pose, shoes and background as close to image 1 as possible. "
            "If the waist is hidden, do not force the top aside, change the pose or invent a "
            "visible belt. Preserve framing and aspect ratio. Do not use the selected polygon "
            "as a precise mask. " + _COMMON_TRYON_RULES + "Output one image."
        )
    if region == 'accessory':
        return (
            "Image 1 is the original person photograph. Image 2 is the catalog photo of the new "
            f"fashion accessory. The selected accessory type is {product_type}; the original "
            f"item description is {description}. Catalog name: {product_name}. "
            f"Brand, if known: {brand or 'unknown'}. Place or replace this same kind of accessory "
            f"at its anatomically appropriate visible location {location}. Preserve its design, "
            "color, material, shape and visible details from image 2. Keep the person's face, "
            "hands, body shape, pose, clothing, shoes, background, framing and aspect ratio as "
            "close to image 1 as possible. If the correct wearing location is not visible or is "
            "unclear, do not distort the body or clothing to force the accessory into view; "
            "leave the person unchanged. Do not add unrelated items. Do not use the selected "
            "polygon as a precise mask. " + _COMMON_TRYON_RULES + "Output one image."
        )
    if region == 'watch':
        watch_location = (f"Prefer the wrist near the selected area ({position}) when identifiable. "
                          if box is not None else "Choose a naturally visible wrist in image 1. ")
        return (
            "Image 1 is the original person photograph. Image 2 is the catalog photo of the new "
            "watch. Replace the watch already worn by the person with the product in image 2; "
            "if no watch is worn, place it naturally on a visible wrist. "
            f"{watch_location}"
            f"Selected watch type: {product_type}. Catalog name: {product_name}. "
            f"Brand, if known: {brand or 'unknown'}. Original item description: {description}. "
            "Preserve the reference watch's dial, color, case, band, material and shape. Fit it "
            "to the wrist's direction, size and perspective with natural contact and shadows. "
            "Keep the face, hands, arms, body shape, pose, clothing, shoes, background, framing "
            "and aspect ratio as close to image 1 as possible. Do not distort fingers or wrists "
            "or significantly change the person's pose to expose the watch. If neither wrist is "
            "visible, do not invent a wrist or force a watch into the scene; leave the person "
            "unchanged. Do not use the selected polygon as a precise mask. " + _COMMON_TRYON_RULES + "Output one image."
        )
    if region == 'bag':
        return (
            "Image 1 is the original person photograph. Image 2 is the catalog photo of the new "
            "bag. Replace an existing bag with the product in image 2, or naturally add it if no "
            f"bag is present {location}. "
            f"Selected bag type: {product_type}. Catalog name: {product_name}. "
            f"Brand, if known: {brand or 'unknown'}. Original item description: {description}. "
            "Place a backpack on the back, a crossbody bag with its strap across the torso from "
            "the shoulder, a shoulder bag on the shoulder, and a tote or handbag in the hand or "
            "on the arm, according to the reference product. Keep its design, color, material, "
            "logo and straps. Do not force an unnatural pose or significantly move the arms to "
            "hold the bag. Keep face, body shape, pose, clothing, shoes, background, framing and "
            "aspect ratio as close to image 1 as possible. Do not add other accessories or people. "
            "Do not use the selected polygon as a precise mask. " + _COMMON_TRYON_RULES + "Output one image."
        )
    if region == 'hat':
        return (
            "Image 1 is the original person photograph. Image 2 is the catalog photo of the new "
            "hat. Replace an existing hat or naturally add the hat from image 2 to the person's "
            f"head {location}. "
            f"Selected hat type: {product_type}. Catalog name: {product_name}. "
            f"Brand, if known: {brand or 'unknown'}. Original item description: {description}. "
            "Match head size, angle and perspective. Position a cap, beanie or bucket hat as its "
            "type requires and add natural contact shadows. Preserve the reference hat's design, "
            "color, material and logo. Keep the face and hairstyle as much as the hat permits. "
            "Do not change facial features, body shape, pose, clothing, shoes, background, "
            "framing or aspect ratio. Do not add other accessories or people. Do not use the "
            "selected polygon as a precise mask. " + _HEAD_ACCESSORY_RULES + _COMMON_TRYON_RULES + "Output one image."
        )
    if region == 'glasses':
        return (
            "Image 1 is the original person photograph. Image 2 is the catalog photo of the new "
            "glasses. Replace existing glasses or naturally add the reference glasses to the "
            f"person's face {location}. "
            f"Selected eyewear type: {product_type}. Catalog name: {product_name}. "
            f"Brand, if known: {brand or 'unknown'}. Original item description: {description}. "
            "Align the frame to the eyes, nose and ears with correct size and perspective. "
            "Preserve frame shape, color, lens color, material and logo as faithfully as possible. "
            "Keep the person's facial features, expression, hair, body shape, pose, clothing, "
            "shoes, background, framing and aspect ratio unchanged. Do not add other accessories "
            "or people. Do not use the selected polygon as a precise mask. " + _HEAD_ACCESSORY_RULES + _EYEWEAR_REVEAL_RULES + _COMMON_TRYON_RULES + "Output one image."
        )
    if region == 'shoes':
        return (
            "Image 1 is the original person photograph. Image 2 is the catalog photo of the new "
            "shoes. Replace the person's existing footwear with the same product from image 2 "
            "on BOTH visible feet" + (f", even if the selected area includes only one foot "
                                      f"({position})" if box is not None else "") +
            ". Fit each shoe to its foot's direction, perspective and pose. "
            f"Selected shoe type: {product_type}. Catalog name: {product_name}. "
            f"Brand, if known: {brand or 'unknown'}. Original shoe description: {description}. "
            "Preserve the new shoes' color, material, logo, laces, soles and design as faithfully "
            "as possible. Blend naturally where pant hems meet the shoes. If a foot is partly "
            "hidden, preserve its position; reveal only skin anatomically needed for "
            "the selected footwear (such as toes in sandals). Keep the person's "
            "face, hair, body shape, leg length, foot positions and pose unchanged. Keep the "
            "existing top, pants or skirt, accessories, background, lighting, framing and aspect "
            "ratio. Do not crop the person or add unrelated body parts or accessories. " + _COMMON_TRYON_RULES + "Output one image."
        )
    if region == 'lower':
        return (
            "Image 1 is the original person photograph. Image 2 is the catalog photo of the new "
            "pants or skirt. Edit image 1 to replace ONLY the person's current lower-body garment "
            f"{location} with the garment shown in image 2. "
            f"Selected garment type: {product_type}. Catalog name: {product_name}. "
            f"Brand, if known: {brand or 'unknown'}. Original garment description: {description}. "
            "Keep the person's face, hair, skin tone, body shape, pose and identity, as well as the "
            "background, lighting, camera framing and aspect ratio. Keep the existing top, shoes "
            "and accessories unchanged. Preserve the reference garment's color, material, pattern, "
            "logo, silhouette and length as faithfully as possible. Fit it naturally around the "
            "waist and legs, with realistic folds and shadows. Handle parts hidden by hands or the "
            "top naturally, including newly exposed legs when changing to shorts or a skirt. "
            "Do not add people, accessories or a new "
            "background. Do not crop the person. " + _COMMON_TRYON_RULES + "Output one image."
        )
    return (
        "Image 1 is the original person photograph. Image 2 is the catalog photo of the new top. "
        "Edit image 1 to replace ONLY the person's current upper-body garment "
        f"{location} with the garment shown in image 2. "
        f"Selected garment type: {product_type}. Catalog name: {product_name}. "
        f"Brand, if known: {brand or 'unknown'}. Original garment description: {description}. "
        "Keep the person's face, hair, skin tone, body shape, pose and identity. "
        "Keep the background, lighting, camera framing, aspect ratio, lower clothing, "
        "shoes and accessories as close to the original as possible. Do not crop the person. "
        "Preserve the reference garment's color, pattern, logo, print, collar and sleeve shape "
        "as faithfully as possible. Fit it naturally to the person's pose with realistic folds "
        "and shadows. Do not add people, accessories or a new background. " + _COMMON_TRYON_RULES + "Output one image."
    )
