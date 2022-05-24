from PIL import Image
import matplotlib.pyplot as plt
import base64

def show_cut(path, left, upper, right, lower):
    """
        原图与所截区域相比较
    :param path: 图片路径
    :param left: 区块左上角位置的像素点离图片左边界的距离
    :param upper：区块左上角位置的像素点离图片上边界的距离
    :param right：区块右下角位置的像素点离图片左边界的距离
    :param lower：区块右下角位置的像素点离图片上边界的距离
     故需满足：lower > upper、right > left
    """

    img = Image.open(path)

    print("This image's size: {}".format(img.size))   #  (W, H)
    
    plt.figure("Image Contrast")

    plt.subplot(1, 2, 1)
    plt.title('origin')
    plt.imshow(img)
    plt.axis('off')

    box = (left, upper, right, lower)
    roi = img.crop(box)

    plt.subplot(1, 2, 2)
    plt.title('roi')
    plt.imshow(roi)
    plt.axis('off')
    plt.show()


def image_cut_save(path, left, upper, right, lower, save_path):
    """
        所截区域图片保存
    :param path: 图片路径
    :param left: 区块左上角位置的像素点离图片左边界的距离
    :param upper：区块左上角位置的像素点离图片上边界的距离
    :param right：区块右下角位置的像素点离图片左边界的距离
    :param lower：区块右下角位置的像素点离图片上边界的距离
     故需满足：lower > upper、right > left
    :param save_path: 所截图片保存位置
    """
    img = Image.open(path)  # 打开图像
    box = (left, upper, right, lower)
    roi = img.crop(box)
    # print('img_stream',img_stream)
    # 保存截取的图片
    roi.save(save_path)
    readImg(save_path)

def readImg(save_path):
   with open(save_path, 'rb') as img_f:
        img_stream = img_f.read()
        img_stream = base64.b64encode(img_stream).decode()
        print('base64',img_stream)
    
if __name__ == '__main__':
    pic_path = '/Users/zhangyifan/Downloads/toy_model/resnet18_cifar10/cifar10.png'
    pic_save_dir_path = '/Users/zhangyifan/Downloads/toy_model/resnet18_cifar10/img/new.png'
    left, upper, right, lower = 0, 0, 32, 32

    for i in range(3):
        left =  (i%7808)*32
        upper = round(i/245)*32
        right = left+32
        lower = upper+32
        name = "img" + str(i)
        pic_save_dir_path = '/Users/zhangyifan/Downloads/toy_model/resnet18_cifar10/img/'+name+'.png'
        print(left,upper,right,lower,name,pic_save_dir_path)
        # show_cut(pic_path, left, upper, right, lower)
        image_cut_save(pic_path, left, upper, right, lower, pic_save_dir_path)
    
    # show_cut(pic_path, left, upper, right, lower)
    # image_cut_save(pic_path, left, upper, right, lower, pic_save_dir_path)
