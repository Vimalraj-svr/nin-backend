import os
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)


async def upload_image(file_bytes: bytes, folder: str = "ninaivugal") -> dict:
    result = cloudinary.uploader.upload(
        file_bytes,
        folder=folder,
        resource_type="image",
        transformation=[{"quality": "auto", "fetch_format": "auto"}],
    )
    return {
        "public_id": result["public_id"],
        "url": result["secure_url"],
        "width": result.get("width"),
        "height": result.get("height"),
    }


async def delete_image(public_id: str) -> None:
    cloudinary.uploader.destroy(public_id)
