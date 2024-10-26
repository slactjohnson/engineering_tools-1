#!/bin/bash
#mention this path to mcs files in usage - /cds/group/pcds/package/wave8/images

usage()
{
    echo "put usage here"
}

remote()
{
    #!/bin/bash
    # shellcheck disable=SC1091
    source /cds/group/pcds/pyps/conda/rogue/etc/profile.d/conda.sh
    if ! conda activate rogue_"$ROGUE_VERSION"; then
        echo "A rogue environment for this version is not available."
        exit 1
    fi
    if [[ ! -e $DEVICE ]]; then
        echo "The device $DEVICE does not exist."
        exit 1
    fi
    export PATH=/cds/group/pcds/engineering_tools/latest-released/scripts/:$PATH
    # shellcheck disable=SC2034
    if ! status=$(/cds/group/pcds/engineering_tools/latest-released/scripts/imgr "$IOCNAME" --status); then
        echo "Unable to get the status of $IOCNAME. Exiting."
        exit 1
    fi

    WAVE8_REPO=/cds/home/t/tjohnson/trunk/workarea/wave8
    if [[ $READ_VERSION -eq 1 ]]; then
        if [[ $status == "RUNNING" ]]; then
            pv=$(/cds/group/pcds/engineering_tools/latest-released/scripts/ioctool "$IOCNAME" pvs | grep FpgaVersion_RBV | awk '{print substr($1,1,length($1)-1)}')
            caget "$pv" -0x
        else
            export PYTHONPATH=$WAVE8_REPO/software/scripts
            python /cds/home/k/kaushikm/w8script/version.py -l "$LANE" --dev "$DEVICE"
        fi
        exit 0
    fi

    if [[ $status == "RUNNING" ]] && ! /cds/group/pcds/engineering_tools/latest-released/scripts/imgr "$IOCNAME" --disable; then
        echo "Unable to disable ioc $IOCNAME. Exiting."
        exit 1
    fi
    #change this to a common path later
    if ! python $WAVE8_REPO/software/scripts/wave8LoadFpga.py --l "$LANE" --dev "$DEVICE" --mcs "$FWPATH"; then
        echo "Firmware update failed."
        exit 1
    fi
    if [[ $status == "RUNNING" ]] && ! /cds/group/pcds/engineering_tools/latest-released/scripts/imgr "$IOCNAME" --enable; then
        echo "Unable to enable ioc $IOCNAME."
        exit 1
    fi
    echo "CHECK THAT THE WAVE8 IOC IS WORKING."
}

# shellcheck disable=SC2034
while getopts "rp:i:d:" option; do
    case $option in
        p)
            FWPATH="$OPTARG"
            ;;
        i)
            IOCNAME="$OPTARG"
            ;;
        d)
            DEVICE="$OPTARG"
            ;;
        r)
            READ_VERSION=1
            ;;
        ?)
            usage
            exit 1
            ;;
    esac
done

if [[ -z $IOCNAME ]]; then
    echo "The ioc name must be provided with the -i flag."
    usage
    exit 1
fi

if [[ $READ_VERSION -ne 1 ]]; then
    if [[ -z $FWPATH ]]; then
        echo "The firmware path must be provided with the -p flag if not using the -r flag to read the version."
        usage
        exit 1
    elif [[ ! -f $FWPATH ]]; then
        echo "The firmware file provided with the -p flag does not exist."
        usage
        exit 1
    elif [[ ! $FWPATH =~ .*mcs ]]; then
        echo "The firmware file provided with the -p argument is not an mcs file. Check that the correct path was provided with the -p flag."
        usage
        exit 1
    fi
else
    if [[ -n $FWPATH ]]; then
        echo "The firmware path cannot be provided with the -r flag."
        usage
        exit 1
    fi
fi

if [[ -z $DEVICE ]]; then
    DEVICE="/dev/datadev_0"
    echo "Device flag -d was not provided, assuming the path is $DEVICE."
fi

if ! CFGPATH=$(/cds/group/pcds/engineering_tools/latest-released/scripts/ioctool "$IOCNAME" cfg); then
    echo "The ioc could not be found. Check that the correct name was provided with the -i flag."
    usage
    exit 1
fi
RELEASE=$(grep "^RELEASE.*" "$CFGPATH" | tail -1 | tr -d '[:space:]')
PARENT=${RELEASE#*=}

if [[ $PARENT == '$$UP(PATH)' ]]; then
    PARENT=$(dirname "$(dirname "$CFGPATH")")
elif [[ ! -d $PARENT ]]; then
    echo "Could not find path to parent ioc."
    exit 1
fi

LANE=$(grep "^PGP_LANE.*" "$CFGPATH" | tail -1 | tr -d '[:space:]')
LANE=${LANE#*=}
if [[ -z $LANE ]]; then
    echo "Could not find lane number."
    exit 1
fi

EPICS_MODULES=$(grep "^EPICS_MODULES.*" "$PARENT"/RELEASE_SITE | tail -1 | tr -d '[:space:]')
EPICS_MODULES=${EPICS_MODULES#*=}

ROGUEREGISTER_MODULE_VERSION=$(grep "^ROGUEREGISTER_MODULE_VERSION.*" "$PARENT"/configure/RELEASE | tail -1 | tr -d '[:space:]')
ROGUEREGISTER_MODULE_VERSION=${ROGUEREGISTER_MODULE_VERSION#*=}

ROGUE_DIR=$(grep "^ROGUE_DIR.*" "$EPICS_MODULES"/rogueRegister/"$ROGUEREGISTER_MODULE_VERSION"/configure/CONFIG_SITE | tail -1 | tr -d '[:space:]')
ROGUE_VERSION=$(echo "$ROGUE_DIR" | grep -Po 'v\d+.\d+.\d+')

# copied from ioctool
INFO=$(grep_ioc "$IOCNAME" all | grep "id:'$IOCNAME'")
if [ -z "$INFO" ]; then
    echo "$IOCNAME could not be found. Exiting..." >&2
    exit 1
fi
HOST=$(echo "$INFO" | sed -n "s/^.*host: '\(\S*\)'.*$/\1/p")

echo "PGP_LANE is $LANE."
echo "Rogue version is $ROGUE_VERSION."
echo "Host is $HOST."

# shellcheck disable=SC2029
ssh "$HOST" "$(typeset -f remote); ROGUE_VERSION=$ROGUE_VERSION DEVICE=$DEVICE IOCNAME=$IOCNAME WAVE8_REPO=$WAVE8_REPO LANE=$LANE FWPATH=$FWPATH READ_VERSION=$READ_VERSION remote"
