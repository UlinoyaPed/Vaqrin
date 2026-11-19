ENTRY(Reset_Handler)

MEMORY {
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 64K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 20K
}

_stack_top = ORIGIN(RAM) + LENGTH(RAM);
_stack_size = 1024;

SECTIONS {
    .vectors : { KEEP(*(.vectors)) } > FLASH
    .text : { *(.text .text.*) *(.rodata .rodata.*) } > FLASH
    .ARM.extab : { *(.ARM.extab*) } > FLASH
    .ARM.exidx : { *(.ARM.exidx*) } > FLASH
    .data : {
        . = ALIGN(4);
        _data_start = .;
        *(.data .data.*)
        . = ALIGN(4);
        _data_end = .;
    } > RAM AT> FLASH
    _data_load = LOADADDR(.data);
    .bss (NOLOAD) : {
        . = ALIGN(4);
        _bss_start = .;
        *(.bss .bss.*) *(COMMON)
        . = ALIGN(4);
        _bss_end = .;
    } > RAM
    ASSERT(_bss_end <= _stack_top - _stack_size, "RAM sections overlap reserved stack")
}
