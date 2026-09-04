1、解压matter.tar.gz 到主目录的 feeds下

2、修改openwrt/feeds.conf.default 文件，在末尾添加
#src-git --force matter https://github.com/project-chip/matter-openwrt.git
src-link matter ../../feeds/matter


3、修改对应项目target.config 文件，

930/openwrt/target/linux/gem6xxx/evb6988_cpe_mt7990_nand/target.config

在末尾添加 配置
# support matter
CONFIG_PACKAGE_matter-netman-mbedtls=y
CONFIG_MBEDTLS_CCM_C=y
CONFIG_MBEDTLS_HKDF_C=y   
CONFIG_PACKAGE_python3=y

4、重新编译openwrt